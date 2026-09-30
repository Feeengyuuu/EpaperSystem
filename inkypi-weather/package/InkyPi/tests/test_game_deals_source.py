from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from plugins.game_deals.source import DealRepository, normalize_deals, redirect_url, safe_thumbnail_url
from runtime.refresh_contracts import TaskCancelled
from utils.http_client import HttpStatusError


NOW = datetime(2026, 9, 30, 1, tzinfo=timezone.utc)


def row(**changes):
    value = {"title": "Ori and the Will of the Wisps", "dealID": "abc%2Bdef%3D",
             "gameID": "209143", "storeID": "1", "salePrice": "2.99",
             "normalPrice": "29.99", "thumb": "https://shared.fastly.steamstatic.com/example.jpg"}
    value.update(changes)
    return value


class FakeHTTP:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def request_json(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.payload, BaseException):
            raise self.payload
        return SimpleNamespace(data=self.payload)


def test_prices_are_decimal_discount_is_rounded_and_redirect_is_not_double_encoded():
    deal, = normalize_deals([row()])
    assert deal["sale_price"] == "2.99"
    assert deal["discount_percent"] == 90
    assert parse_qs(urlsplit(deal["redirect_url"]).query)["dealID"] == ["abc+def="]
    assert "historical_low" not in deal


@pytest.mark.parametrize("price", ["NaN", "Infinity", "-1", None])
def test_invalid_price_is_not_rendered_as_a_free_deal(price):
    with pytest.raises(ValueError):
        normalize_deals([row(salePrice=price)])


def test_deduplicates_games_and_filters_other_stores_or_ended_sales():
    deals = normalize_deals([row(storeID="2"), row(salePrice="29.99"), row(), row(dealID="other")])
    assert len(deals) == 1
    assert deals[0]["store_id"] == "1"


@pytest.mark.parametrize("url", ["http://shared.fastly.steamstatic.com/a", "https://127.0.0.1/a",
                                "https://steamstatic.com.evil.test/a", "https://x@steamstatic.com/a"])
def test_cover_urls_are_limited_to_known_https_hosts(url):
    assert safe_thumbnail_url(url) == ""


def test_success_cache_refreshes_only_after_two_hours_and_is_bounded(tmp_path):
    http = FakeHTTP([row()])
    repo = DealRepository(tmp_path, http=http)
    assert repo.load({}, now=NOW).state == "live"
    assert repo.load({}, now=NOW + timedelta(seconds=7199)).state == "fresh_cache"
    assert len(http.calls) == 1
    assert repo.load({}, now=NOW + timedelta(seconds=7200)).state == "live"
    assert len(http.calls) == 2
    kwargs = http.calls[0][1]
    assert kwargs["params"]["storeID"] == "1"
    assert kwargs["max_bytes"] == 128 * 1024
    assert kwargs["timeout"] == 12
    assert kwargs["allow_redirects"] is False
    assert "EpaperSystem" in kwargs["headers"]["User-Agent"]


def test_cached_display_reads_do_not_fetch_or_write_even_when_expired(tmp_path):
    http = FakeHTTP([row()])
    repo = DealRepository(tmp_path, http=http)
    repo.load({}, now=NOW)
    path = tmp_path / "deals-steam.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    snapshot = repo.load({}, now=NOW + timedelta(hours=3), cached_only=True, force=True)
    assert snapshot.state == "stale"
    assert len(http.calls) == 1
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_failed_refresh_retains_original_source_time_and_marks_stale(tmp_path):
    http = FakeHTTP([row()])
    repo = DealRepository(tmp_path, http=http)
    repo.load({}, now=NOW)
    http.payload = RuntimeError("offline")
    snapshot = repo.load({}, now=NOW + timedelta(hours=2))
    assert snapshot.state == "stale"
    assert snapshot.fetched_at == NOW
    assert len(snapshot.deals) == 1
    assert repo.load({}, now=NOW + timedelta(hours=25), cached_only=True).state == "unavailable"


def test_genuine_empty_result_replaces_old_offers_and_is_fresh(tmp_path):
    http = FakeHTTP([row()])
    repo = DealRepository(tmp_path, http=http)
    repo.load({}, now=NOW)
    http.payload = []
    snapshot = repo.load({}, now=NOW + timedelta(hours=2))
    assert snapshot.state == "live"
    assert snapshot.deals == ()
    assert snapshot.fetched_at == NOW + timedelta(hours=2)


def test_rate_limit_creates_cooldown_that_force_does_not_bypass(tmp_path):
    http = FakeHTTP(HttpStatusError("GET", "https://www.cheapshark.com/api/1.0/deals", 429))
    repo = DealRepository(tmp_path, http=http)
    assert repo.load({}, now=NOW).state == "unavailable"
    repo.load({}, now=NOW + timedelta(minutes=59), force=True)
    assert len(http.calls) == 1
    repo.load({}, now=NOW + timedelta(hours=1))
    assert len(http.calls) == 2


def test_cancellation_propagates_without_publishing_failed_cache(tmp_path):
    repo = DealRepository(tmp_path, http=FakeHTTP(TaskCancelled("cancelled")))
    with pytest.raises(TaskCancelled):
        repo.load({}, now=NOW)
    assert list(tmp_path.iterdir()) == []


def test_store_scope_cannot_escape_user_approved_us_steam_filter(tmp_path):
    http = FakeHTTP([row()])
    repo = DealRepository(tmp_path, http=http)
    repo.load({"storeScope": "all"}, now=NOW)
    assert http.calls[-1][1]["params"]["storeID"] == "1"


def test_source_keeps_six_distinct_offers_but_not_the_entire_response():
    deals = normalize_deals([row(gameID=str(number), title=f"Game {number}") for number in range(20)])
    assert len(deals) == 6
