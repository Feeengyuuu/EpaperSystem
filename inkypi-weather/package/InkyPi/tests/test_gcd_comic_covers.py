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
