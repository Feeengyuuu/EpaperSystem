import sys
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.gcd_comic_covers.gcd_comic_covers import GcdComicCovers, GcdCoverImageUnavailable, _GcdMonthlyParser  # noqa: E402


class DeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key, default=None):
        if key == "timezone":
            return "America/Los_Angeles"
        if key == "orientation":
            return "horizontal"
        return default


class StaticLoader:
    def from_url(self, url, dimensions, timeout_ms=40000, resize=True, headers=None, focus_crop=False):
        return Image.new("RGB", (320, 480), "white")


class MissingImageLoader:
    def from_url(self, url, dimensions, timeout_ms=40000, resize=True, headers=None, focus_crop=False):
        return None


def make_plugin(tmp_path, monkeypatch):
    plugin = GcdComicCovers({"id": "gcd_comic_covers"})
    plugin.image_loader = StaticLoader()
    def download_cover_image(cover_url, candidate, detail):
        image = plugin.image_loader.from_url(cover_url, (800, 480), resize=False)
        if not image:
            raise GcdCoverImageUnavailable("cover image could not be loaded", candidate, detail, cover_url)
        return image
    monkeypatch.setattr(plugin, "_download_cover_image", download_cover_image)
    monkeypatch.setenv("INKYPI_GCD_COMIC_COVERS_CACHE", str(tmp_path))
    return plugin


def test_candidate_order_prefers_exact_day_before_month_fallback(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    candidates = [
        {"issue_id": "1", "match_quality": "month_fallback"},
        {"issue_id": "2", "match_quality": "exact_day"},
        {"issue_id": "3", "match_quality": "month_fallback"},
    ]

    ordered = plugin._candidate_order(candidates, {"version": "gcd-comic-covers-state-v1", "date_buckets": {}}, today)

    assert ordered[0]["issue_id"] == "2"


def test_candidate_order_keeps_month_fallback_after_exact_day(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    candidates = [
        {"issue_id": "1", "match_quality": "month_fallback"},
        {"issue_id": "2", "match_quality": "exact_day"},
        {"issue_id": "3", "match_quality": "month_fallback"},
    ]

    ordered = plugin._candidate_order(candidates, {"version": "gcd-comic-covers-state-v1", "date_buckets": {}}, today)

    assert ordered[0]["issue_id"] == "2"
    assert {item["issue_id"] for item in ordered[1:]} == {"1", "3"}


def test_candidate_order_uses_month_when_exact_day_is_in_waste_pit(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    state = {
        "version": "gcd-comic-covers-state-v1",
        "date_buckets": {"05-30": {"seen_issue_ids": ["2"]}},
    }
    candidates = [
        {"issue_id": "1", "match_quality": "month_fallback"},
        {"issue_id": "2", "match_quality": "exact_day"},
    ]

    ordered = plugin._candidate_order(candidates, state, today)

    assert ordered[0]["issue_id"] == "1"


def test_waste_pit_uses_issue_id_so_variants_can_each_display(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    state = {"version": "gcd-comic-covers-state-v1", "date_buckets": {}}
    variant_a = {"issue_id": "10", "match_quality": "exact_day"}
    variant_b = {"issue_id": "11", "match_quality": "exact_day"}

    plugin._mark_seen(state, today, {"issue_id": "10"})
    ordered = plugin._candidate_order([variant_a, variant_b], state, today)

    assert [item["issue_id"] for item in ordered] == ["11"]


def test_candidate_order_tries_candidates_with_cover_urls_first(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    candidates = [
        {"issue_id": "1", "match_quality": "exact_day"},
        {"issue_id": "2", "match_quality": "exact_day", "cover_url": "https://example.com/cover.png"},
    ]

    ordered = plugin._candidate_order(candidates, {"version": "gcd-comic-covers-state-v1", "date_buckets": {}}, today)

    assert ordered[0]["issue_id"] == "2"


def test_candidate_order_prefers_comic_vine_recent_before_gcd_exact(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 6, 3)
    candidates = [
        {"source": "gcd", "issue_id": "1", "match_quality": "exact_day", "cover_url": "https://example.com/gcd.jpg"},
        {"source": "comicvine", "issue_id": "comicvine:2", "match_quality": "comicvine_recent", "cover_url": "https://example.com/cv.jpg"},
    ]

    ordered = plugin._candidate_order(candidates, {"version": "gcd-comic-covers-state-v1", "date_buckets": {}}, today)

    assert ordered[0]["issue_id"] == "comicvine:2"


def test_candidate_pool_defaults_to_comic_vine_with_gcd_fallback(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 6, 4)
    monkeypatch.setattr(
        plugin,
        "_gcd_candidate_pool",
        lambda _settings, _today: [{
            "source": "gcd",
            "issue_id": "gcd:1",
            "match_quality": "exact_day",
            "cover_url": "https://example.com/gcd.jpg",
        }],
    )
    monkeypatch.setattr(
        plugin,
        "_comic_vine_candidate_pool",
        lambda _settings, _today: [{
            "source": "comicvine",
            "issue_id": "comicvine:2",
            "match_quality": "comicvine_recent",
            "cover_url": "https://example.com/cv.jpg",
        }],
    )

    candidates = plugin._candidate_pool({}, today)

    assert [candidate["issue_id"] for candidate in candidates] == ["comicvine:2", "gcd:1"]


def test_candidate_order_recycles_comic_vine_before_gcd_when_priority_seen(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 6, 4)
    state = {
        "version": "gcd-comic-covers-state-v1",
        "date_buckets": {
            "06-04": {
                "seen_issue_ids": ["comicvine:1", "comicvine:2"],
                "last_issue_id": "comicvine:2",
            },
        },
    }
    candidates = [
        {"source": "comicvine", "issue_id": "comicvine:1", "match_quality": "comicvine_recent", "cover_url": "https://example.com/cv1.jpg"},
        {"source": "comicvine", "issue_id": "comicvine:2", "match_quality": "comicvine_recent", "cover_url": "https://example.com/cv2.jpg"},
        {"source": "gcd", "issue_id": "gcd:1", "match_quality": "exact_day", "cover_url": "https://example.com/gcd.jpg"},
    ]

    ordered = plugin._candidate_order(candidates, state, today)

    assert ordered[0]["issue_id"] == "comicvine:1"


def test_waste_pit_resets_after_pool_is_exhausted(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    state = {
        "version": "gcd-comic-covers-state-v1",
        "date_buckets": {"05-30": {"seen_issue_ids": ["10", "11"], "last_issue_id": "11"}},
    }
    candidates = [
        {"issue_id": "10", "match_quality": "exact_day"},
        {"issue_id": "11", "match_quality": "exact_day"},
    ]

    ordered = plugin._candidate_order(candidates, state, today)

    assert {item["issue_id"] for item in ordered} == {"10", "11"}
    assert state["date_buckets"]["05-30"]["seen_issue_ids"] == []


def test_filter_candidates_accepts_exact_day_and_month_only(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    candidates = [
        {"issue_id": "1", "country": "United States", "on_sale_date": "1942-05-30"},
        {"issue_id": "2", "country": "us", "on_sale_date": "1942-05"},
        {"issue_id": "3", "country": "us", "on_sale_date": "1942-06-30"},
        {"issue_id": "4", "country": "us", "on_sale_date": "2026-05-31"},
        {"issue_id": "5", "country": "us", "on_sale_date": "2026-05-30"},
    ]

    filtered = plugin._filter_candidates(candidates, {"countryCodes": "us"}, today)

    assert [(item["issue_id"], item["match_quality"]) for item in filtered] == [
        ("1", "exact_day"),
        ("2", "month_fallback"),
        ("5", "exact_day"),
    ]


def test_default_year_range_runs_to_current_year(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    years = plugin._target_years({}, date(2026, 5, 30))

    assert years[0] == 1938
    assert years[-1] == 2026


def test_candidate_pool_fetches_current_year_first_and_pauses_backfill(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    fetched_years = []

    monkeypatch.setattr(plugin, "_target_years", lambda settings, current_date: [2024, 2025, 2026])
    monkeypatch.setattr(plugin, "_read_month_cache", lambda year, month, day=None: None)
    monkeypatch.setattr(plugin, "_write_month_cache", lambda year, month, candidates, day=None: None)

    def fake_fetch(year, month, day=None):
        fetched_years.append(year)
        return [
            {"issue_id": f"{year}-{index}", "country": "us", "on_sale_date": f"{year:04d}-05-30"}
            for index in range(130)
        ]

    monkeypatch.setattr(plugin, "_fetch_month_candidates", fake_fetch)

    candidates = plugin._candidate_pool({"maxYearsPerRefresh": "10"}, today)

    assert fetched_years == [2026]
    assert len(candidates) == 130


def test_monthly_html_parser_extracts_issue_id_date_and_cover():
    parser = _GcdMonthlyParser("https://www.comics.org/on_sale_monthly/1942/month/5/")
    parser.feed(
        """
        <table>
          <tr>
            <td><img src="/covers/preview/abc.jpg" alt="preview"></td>
            <td><img src="/flags/us.png" alt="United States"></td>
            <td><a href="/issue/12345/">Captain Example #7</a></td>
            <td>1942-05-30</td>
          </tr>
        </table>
        """
    )
    plugin = GcdComicCovers({"id": "gcd_comic_covers"})

    candidates = []
    for row in parser.rows:
        candidates.append({
            "issue_id": row["issue_id"],
            "country": row["country"],
            "on_sale_date": plugin._date_from_text(" ".join(row["text"]), 1942, 5),
            "cover_url": row["cover_url"],
        })

    assert candidates == [{
        "issue_id": "12345",
        "country": "us",
        "on_sale_date": "1942-05-30",
        "cover_url": "https://www.comics.org/covers/preview/abc.jpg",
    }]


def test_day_candidate_fetch_uses_weekly_api(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    target = date(2026, 5, 29)
    iso_year, iso_week, _weekday = target.isocalendar()
    calls = []

    def fake_fetch_json(url):
        calls.append(url)
        if len(calls) == 1:
            return {
                "results": [{
                    "api_url": "https://www.comics.org/api/issue/256114/",
                    "series_name": "Captain Example",
                    "descriptor": "7",
                    "publication_date": "May 2026",
                }],
                "next": "https://www.comics.org/api/issue/on_sale_weekly/2026/week/22?page=2",
            }
        return {
            "results": [{
                "api_url": "https://www.comics.org/api/issue/256115/",
                "series_name": "Second Example",
                "descriptor": "8",
            }],
            "next": None,
        }

    monkeypatch.setattr(plugin, "_fetch_json", fake_fetch_json)

    candidates = plugin._fetch_month_candidates(target.year, target.month, target.day)

    assert f"/api/issue/on_sale_weekly/{iso_year}/week/{iso_week}" in calls[0]
    assert [candidate["issue_id"] for candidate in candidates] == ["256114", "256115"]
    assert {candidate["target_date"] for candidate in candidates} == {"2026-05-29"}


def test_cover_url_normalization_removes_duplicate_path_slashes(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    url = plugin._normalize_cover_url("https://files1.comics.org//img/gcd/covers_by_id/48/w400/48980.jpg")

    assert url == "https://files1.comics.org/img/gcd/covers_by_id/48/w400/48980.jpg"


def test_generate_image_uses_metadata_cover_when_source_image_is_blocked(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin.image_loader = MissingImageLoader()
    monkeypatch.setattr(plugin, "_current_date", lambda _device_config: date(2026, 5, 30))
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda _settings, _today: [{
            "issue_id": "28815",
            "country": "us",
            "on_sale_date": "1975-05-30",
            "match_quality": "exact_day",
        }],
    )
    monkeypatch.setattr(
        plugin,
        "_issue_detail",
        lambda _candidate: {
            "issue_id": "28815",
            "series_name": "Tales of Evil",
            "issue_number": "3",
            "publisher": "Atlas Comics",
            "on_sale_date": "1975-05-30",
            "cover_url": "https://files1.comics.org//img/gcd/covers_by_id/48/w400/48980.jpg",
            "cover_credits": "Pencils: Rich Buckler; Inks: Rich Buckler",
        },
    )
    monkeypatch.setattr("plugins.gcd_comic_covers.gcd_comic_covers.write_context", lambda *args, **kwargs: None)

    image = plugin.generate_image({"maxCoverAttempts": "1"}, DeviceConfig())

    assert image.size == (800, 480)
    assert image.getbbox() is not None


def test_generate_image_limits_blocked_cover_attempts(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin.image_loader = MissingImageLoader()
    attempted = []
    monkeypatch.setattr(plugin, "_current_date", lambda _device_config: date(2026, 5, 30))
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda _settings, _today: [
            {"issue_id": str(index), "country": "us", "on_sale_date": "1975-05-30", "match_quality": "exact_day"}
            for index in range(6)
        ],
    )

    def fake_issue_detail(candidate):
        attempted.append(candidate["issue_id"])
        return {
            "issue_id": candidate["issue_id"],
            "series_name": "Blocked Example",
            "issue_number": candidate["issue_id"],
            "country": "us",
            "on_sale_date": "1975-05-30",
            "cover_url": f"https://files1.comics.org/img/gcd/covers_by_id/0/w400/{candidate['issue_id']}.jpg",
        }

    monkeypatch.setattr(plugin, "_issue_detail", fake_issue_detail)
    monkeypatch.setattr("plugins.gcd_comic_covers.gcd_comic_covers.write_context", lambda *args, **kwargs: None)

    plugin.generate_image({"maxCoverAttempts": "2"}, DeviceConfig())

    assert len(attempted) == 2
    assert set(attempted).issubset({str(index) for index in range(6)})


def test_generate_image_uses_candidate_metadata_when_issue_detail_is_rate_limited(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, "_current_date", lambda _device_config: date(2026, 5, 30))
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda _settings, _today: [{
            "issue_id": "rate-limited",
            "series_name": "Candidate Only",
            "issue_number": "12",
            "publisher": "Example Publisher",
            "on_sale_date": "1975-05-30",
            "match_quality": "exact_day",
        }],
    )
    monkeypatch.setattr(plugin, "_load_cover", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("429 Too Many Requests")))
    monkeypatch.setattr("plugins.gcd_comic_covers.gcd_comic_covers.write_context", lambda *args, **kwargs: None)

    image = plugin.generate_image({"maxCoverAttempts": "1"}, DeviceConfig())

    assert image.size == (800, 480)
    assert image.getbbox() is not None


def test_generate_image_defaults_to_plain_triptych_and_marks_all_covers(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, "_current_date", lambda _device_config: date(2026, 5, 30))
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda _settings, _today: [
            {"issue_id": "1", "country": "us", "on_sale_date": "1975-05-30", "match_quality": "exact_day"},
            {"issue_id": "2", "country": "us", "on_sale_date": "1975-05-30", "match_quality": "exact_day"},
            {"issue_id": "3", "country": "us", "on_sale_date": "1975-05-30", "match_quality": "exact_day"},
        ],
    )
    monkeypatch.setattr(plugin, "_candidate_order", lambda candidates, _state, _today: candidates)
    colors = {
        "1": (220, 0, 0),
        "2": (0, 160, 0),
        "3": (0, 0, 220),
    }

    def fake_load_cover(candidate, _dimensions, _settings):
        issue_id = candidate["issue_id"]
        return {
            **candidate,
            "series_name": f"Series {issue_id}",
            "issue_number": issue_id,
            "cover_url": f"https://example.com/{issue_id}.jpg",
            "date_label": "1975-05-30",
            "image": Image.new("RGB", (200, 400), colors[issue_id]),
        }

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)
    monkeypatch.setattr("plugins.gcd_comic_covers.gcd_comic_covers.write_context", lambda *args, **kwargs: None)

    image = plugin.generate_image({}, DeviceConfig())
    state = plugin._read_state()

    assert image.size == (800, 480)
    assert image.getpixel((133, 240)) == colors["1"]
    assert image.getpixel((399, 240)) == colors["2"]
    assert image.getpixel((666, 240)) == colors["3"]
    assert state["date_buckets"]["05-30"]["seen_issue_ids"] == ["1", "2", "3"]
    assert state["date_buckets"]["05-30"]["last_issue_id"] == "3"


def test_triptych_generation_prefers_portrait_covers_over_wide_strips(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    candidates = [
        {"issue_id": "wide"},
        {"issue_id": "red"},
        {"issue_id": "green"},
        {"issue_id": "blue"},
    ]
    colors = {
        "wide": (230, 180, 0),
        "red": (220, 0, 0),
        "green": (0, 160, 0),
        "blue": (0, 0, 220),
    }

    def fake_load_cover(candidate, _dimensions, _settings):
        issue_id = candidate["issue_id"]
        size = (500, 160) if issue_id == "wide" else (200, 400)
        return {
            **candidate,
            "series_name": issue_id,
            "issue_number": "1",
            "date_label": "1975-05-30",
            "cover_url": f"https://example.com/{issue_id}.jpg",
            "image": Image.new("RGB", size, colors[issue_id]),
        }

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)
    monkeypatch.setattr("plugins.gcd_comic_covers.gcd_comic_covers.write_context", lambda *args, **kwargs: None)

    image = plugin._generate_triptych_image(candidates, {}, today, (800, 480), {"backgroundColor": "white"}, 4)
    state = plugin._read_state()

    assert image.getpixel((133, 240)) == colors["red"]
    assert image.getpixel((399, 240)) == colors["green"]
    assert image.getpixel((666, 240)) == colors["blue"]
    assert state["date_buckets"]["05-30"]["seen_issue_ids"] == ["red", "green", "blue"]


def test_triptych_mode_renders_available_cover_without_info_label(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    cover = {
        "series_name": "Label Should Not Render",
        "issue_number": "9",
        "date_label": "1975-05-30",
        "image": Image.new("RGB", (200, 400), (220, 0, 0)),
    }

    image = plugin._compose_triptych_display_image([cover], (800, 480), {"backgroundColor": "white"})

    assert image.size == (800, 480)
    assert image.getpixel((399, 240)) == (220, 0, 0)
    assert image.getpixel((20, 460)) != (255, 255, 255)


def test_triptych_mode_expands_two_covers_to_fill_screen_width(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    covers = [
        {"image": Image.new("RGB", (200, 400), (220, 0, 0))},
        {"image": Image.new("RGB", (200, 400), (0, 0, 220))},
    ]

    image = plugin._compose_triptych_display_image(covers, (800, 480), {"backgroundColor": "white"})

    assert image.size == (800, 480)
    assert image.getpixel((100, 240)) == (220, 0, 0)
    assert image.getpixel((700, 240)) == (0, 0, 220)


def test_date_cache_path_is_day_scoped(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    assert plugin._month_cache_path(2026, 5, 29).as_posix().endswith("/dates/2026-05-29.json")


def test_validate_detail_date_accepts_month_fallback_and_rejects_other_month(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    candidate = {"target_date": "2026-05-29"}

    plugin._validate_detail_date({"on_sale_date": "2026-05-29"}, candidate)
    plugin._validate_detail_date({"on_sale_date": "2026-05-01"}, candidate)

    with pytest.raises(RuntimeError):
        plugin._validate_detail_date({"on_sale_date": "2026-06-01"}, candidate)


def test_validate_detail_date_skips_comic_vine_recent_candidates(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    candidate = {
        "source": "comicvine",
        "match_quality": "comicvine_recent",
        "target_date": "2026-06-03",
    }

    plugin._validate_detail_date({"source": "comicvine", "on_sale_date": "2023-07-21"}, candidate)


def test_default_fit_mode_rotates_portrait_cover_counterclockwise(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    portrait = Image.new("RGB", (320, 480), "blue")
    for y in range(480):
        for x in range(160, 320):
            portrait.putpixel((x, y), (255, 0, 0))

    image = plugin._fit_cover(
        portrait,
        (800, 480),
        {"backgroundStyle": "plain", "backgroundColor": "white", "showInfoLabel": "false"},
        {},
    )

    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert image.getpixel((799, 0)) == (255, 0, 0)
    assert image.getpixel((0, 479)) == (0, 0, 255)


def test_comic_vine_recent_candidates_normalize_issue_and_image(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 6, 3)

    def fake_comic_vine_get(path, api_key, params):
        assert path == "issues/"
        assert api_key == "secret"
        assert params["sort"] == "date_added:desc"
        return {
            "status_code": 1,
            "results": [{
                "id": 123,
                "api_detail_url": "https://comicvine.gamespot.com/api/issue/4000-123/",
                "site_detail_url": "https://comicvine.gamespot.com/example/",
                "name": "The Test Issue",
                "issue_number": "7",
                "cover_date": "2026-06-01",
                "store_date": "2026-06-03",
                "date_added": "2026-06-03 12:30:00",
                "volume": {"name": "Test Volume"},
                "image": {"super_url": "https://comicvine.gamespot.com/a/uploads/scale_large/1/123.jpg"},
            }],
        }

    monkeypatch.setattr(plugin, "_comic_vine_get", fake_comic_vine_get)

    candidates = plugin._fetch_comic_vine_recent_candidates("secret", today, 10)

    assert candidates == [{
        "source": "comicvine",
        "source_label": "Comic Vine",
        "issue_id": "comicvine:123",
        "comic_vine_id": "123",
        "series_name": "Test Volume",
        "issue_number": "7",
        "title": "The Test Issue",
        "publisher": "",
        "country": "",
        "language": "",
        "on_sale_date": "2026-06-03",
        "store_date": "2026-06-03",
        "cover_date": "2026-06-01",
        "date_added": "2026-06-03 12:30:00",
        "cover_url": "https://comicvine.gamespot.com/a/uploads/scale_large/1/123.jpg",
        "page_url": "https://comicvine.gamespot.com/example/",
        "api_url": "https://comicvine.gamespot.com/api/issue/4000-123/",
        "target_date": "2026-06-03",
        "year": 2026,
        "match_quality": "comicvine_recent",
    }]


def test_mixed_source_mode_prepends_comic_vine_candidates(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 6, 3)
    monkeypatch.setattr(plugin, "_gcd_candidate_pool", lambda _settings, _today: [{"source": "gcd", "issue_id": "1"}])
    monkeypatch.setattr(plugin, "_comic_vine_candidate_pool", lambda _settings, _today: [{"source": "comicvine", "issue_id": "comicvine:2"}])

    candidates = plugin._candidate_pool({"sourceMode": "mixed"}, today)

    assert [candidate["issue_id"] for candidate in candidates] == ["comicvine:2", "1"]


def test_source_mode_accepts_settings_html_comicvine_value(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    assert plugin._source_mode({"sourceMode": "comicvine"}) == "comicvine"


def test_comic_vine_issue_cache_path_is_windows_safe(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    path = plugin._issue_cache_path("comicvine:123")

    assert "comicvine_123" in path.name
    assert ":" not in path.name
