from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from plugins.game_deals.facts import GAMES_URL, STORES_URL, low_mark
from plugins.game_deals.names import ITEMS_URL
from plugins.game_deals.source import API_URL, DealRepository, normalize_deals, redirect_url, safe_thumbnail_url
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
    """Answers every request with one payload; ``deal_calls`` are the CheapShark offer requests."""

    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def request_json(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.payload, BaseException):
            raise self.payload
        return SimpleNamespace(data=self.payload)

    @property
    def deal_calls(self):
        return [call for call in self.calls if call[0][1] == API_URL]


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
    assert len(http.deal_calls) == 1
    assert repo.load({}, now=NOW + timedelta(seconds=7200)).state == "live"
    assert len(http.deal_calls) == 2
    kwargs = http.deal_calls[0][1]
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
    assert len(http.deal_calls) == 1
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
    assert http.deal_calls[-1][1]["params"]["storeID"] == "1"


def test_source_keeps_ten_distinct_offers_but_not_the_entire_response():
    deals = normalize_deals([row(gameID=str(number), title=f"Game {number}") for number in range(20)])
    assert len(deals) == 10


def steam_items(*items):
    return {"response": {"store_items": [
        {"appid": appid, "success": success, "name": name} for appid, name, success in items
    ]}}


class StoreHTTP:
    """CheapShark offers, its store list and all-time lows, plus Steam's batch store-name lookup."""

    def __init__(self, deals, names, stores=None, games=None):
        self.deals, self.names, self.calls = deals, names, []
        self.stores = stores if stores is not None else [{"storeID": "1", "storeName": "Steam"}]
        self.games = games if games is not None else {}

    def request_json(self, method, url, **kwargs):
        self.calls.append((url, kwargs))
        payload = {API_URL: self.deals, STORES_URL: self.stores, GAMES_URL: self.games}.get(url, self.names)
        if isinstance(payload, BaseException):
            raise payload
        return SimpleNamespace(data=payload)


def store_rows():
    return [row(gameID="1", title="Hogwarts Legacy", steamAppID="990080"),
            row(gameID="2", title="Undertale", steamAppID="391540"),
            row(gameID="3", title="Mortal Kombat 11 Ultimate", steamAppID="518699"),
            row(gameID="4", title="Bundle Without App", steamAppID=None)]


def test_steam_simplified_chinese_name_overlays_title_only_when_it_exists(tmp_path):
    http = StoreHTTP(store_rows(), steam_items((990080, "霍格沃茨之遗™", 1), (391540, "Undertale", 1), (0, "", 15)))
    snapshot = DealRepository(tmp_path, http=http).load({}, now=NOW)
    assert [(deal["title"], deal["title_zh"]) for deal in snapshot.deals] == [
        ("Hogwarts Legacy", "霍格沃茨之遗"), ("Undertale", ""), ("Mortal Kombat 11 Ultimate", ""), ("Bundle Without App", "")]
    url, kwargs = http.calls[1]
    assert url == ITEMS_URL and kwargs["allow_redirects"] is False and "EpaperSystem" in kwargs["headers"]["User-Agent"]
    query = json.loads(kwargs["params"]["input_json"])
    assert [item["appid"] for item in query["ids"]] == [990080, 391540, 518699]
    assert query["context"]["language"] == "schinese"
    # The cached page keeps the overlay for provider-free redraws.
    cached = DealRepository(tmp_path, http=http).load({}, now=NOW + timedelta(minutes=5), cached_only=True)
    assert cached.deals[0]["title_zh"] == "霍格沃茨之遗"


def test_chinese_names_are_remembered_and_steam_failure_keeps_english(tmp_path):
    http = StoreHTTP(store_rows(), steam_items((990080, "霍格沃茨之遗", 1), (391540, "Undertale", 1)))
    repo = DealRepository(tmp_path, http=http)
    repo.load({}, now=NOW)
    http.names = RuntimeError("Steam unavailable")
    later = repo.load({}, now=NOW + timedelta(hours=2))
    # Names (found and absent) and the store list are cached for 30 days, so neither is asked again.
    assert [url for url, _ in http.calls] == [API_URL, ITEMS_URL, STORES_URL, GAMES_URL, API_URL, GAMES_URL]
    assert later.state == "live" and later.deals[0]["title_zh"] == "霍格沃茨之遗"
    fresh = DealRepository(tmp_path / "other", http=http).load({}, now=NOW)
    assert fresh.state == "live" and all(deal["title_zh"] == "" for deal in fresh.deals)
    assert not (tmp_path / "other" / "names-schinese.json").exists()


def test_cached_chinese_title_must_actually_be_chinese(tmp_path):
    http = StoreHTTP(store_rows(), steam_items((990080, "霍格沃茨之遗", 1)))
    repo = DealRepository(tmp_path, http=http)
    repo.load({}, now=NOW)
    path = tmp_path / "deals-steam.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["deals"][0]["title_zh"] = "Injected English"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert repo.load({}, now=NOW, cached_only=True).deals[0]["title_zh"] == ""


def test_cache_from_before_chinese_names_is_refetched_once(tmp_path):
    path = tmp_path / "deals-steam.json"
    path.write_text(json.dumps({"schema": 1, "scope": "steam", "currency": "USD", "fetched_at": NOW.isoformat(),
                                "deals": [{"title": "Hogwarts Legacy", "deal_id": "d", "game_id": "1", "store_id": "1",
                                           "sale_price": "5.99", "normal_price": "59.99"}]}), encoding="utf-8")
    http = StoreHTTP([row(gameID="1", title="Hogwarts Legacy", steamAppID="990080")], steam_items((990080, "霍格沃茨之遗", 1)))
    repo = DealRepository(tmp_path, http=http)
    assert repo.load({}, now=NOW + timedelta(minutes=5), cached_only=True).deals[0]["title_zh"] == ""
    assert repo.load({}, now=NOW + timedelta(minutes=5)).deals[0]["title_zh"] == "霍格沃茨之遗"
    assert repo.load({}, now=NOW + timedelta(minutes=10)).state == "fresh_cache"
    assert [url for url, _ in http.calls] == [API_URL, ITEMS_URL, STORES_URL, GAMES_URL]


def test_previous_six_offer_cache_stays_readable(tmp_path):
    http = FakeHTTP([row(gameID=str(number), title=f"Game {number}") for number in range(6)])
    repo = DealRepository(tmp_path, http=http)
    repo.load({}, now=NOW)
    snapshot = repo.load({}, now=NOW + timedelta(minutes=5), cached_only=True)
    assert snapshot.state == "fresh_cache"
    assert len(snapshot.deals) == 6


def test_reviews_come_from_the_offer_row_and_survive_the_cache(tmp_path):
    rows = [row(gameID="1", metacriticScore="89", steamRatingText="Very Positive", steamRatingPercent="90",
                steamRatingCount="123165"),
            row(gameID="2", metacriticScore="0", steamRatingText=None, steamRatingPercent="0", steamRatingCount="0"),
            row(gameID="3", metacriticScore="250", steamRatingText="Glowing", steamRatingPercent="99",
                steamRatingCount="5")]
    repo = DealRepository(tmp_path, http=StoreHTTP(rows, steam_items()))
    live = repo.load({}, now=NOW)
    cached = repo.load({}, now=NOW + timedelta(minutes=5), cached_only=True)
    for snapshot in (live, cached):
        rated, unrated, odd = snapshot.deals
        assert (rated["metacritic"], rated["steam_rating"], rated["steam_rating_percent"],
                rated["steam_rating_count"]) == (89, "Very Positive", 90, 123165)
        # Missing or unknown values are absent, never a zero score on the page.
        assert (unrated["metacritic"], unrated["steam_rating"], unrated["steam_rating_percent"]) == (0, "", 0)
        assert (odd["metacritic"], odd["steam_rating"], odd["steam_rating_percent"]) == (0, "", 0)


def test_all_time_lows_come_from_one_games_request_and_the_store_list_is_kept(tmp_path):
    rows = [row(gameID="1", salePrice="1.99"), row(gameID="2", salePrice="2.99"), row(gameID="3", salePrice="4.99")]
    games = {"1": {"cheapestPriceEver": {"price": "1.99", "date": 1793651134}},
             "2": {"cheapestPriceEver": {"price": "2.24", "date": 1719576446}},
             "3": {"info": {"title": "No history"}}}
    stores = [{"storeID": "1", "storeName": "Steam"}, {"storeID": "25", "storeName": "Epic Games Store"}]
    http = StoreHTTP(rows, steam_items(), stores=stores, games=games)
    repo = DealRepository(tmp_path, http=http)
    snapshot = repo.load({}, now=NOW)
    (_, games_call), = [call for call in http.calls if call[0] == GAMES_URL]
    assert games_call["params"] == {"ids": "1,2,3"} and games_call["allow_redirects"] is False
    assert [(deal["lowest_price"], deal["lowest_at"]) for deal in snapshot.deals] == [
        ("1.99", 1793651134), ("2.24", 1719576446), ("", 0)]
    assert [low_mark(deal) for deal in snapshot.deals] == [("low", ""), ("above", "2.24"), ("", "")]
    assert snapshot.deals[0]["store_name"] == "Steam"
    assert json.loads((tmp_path / "stores.json").read_text(encoding="utf-8"))["stores"] == {
        "1": "Steam", "25": "Epic Games Store"}
    cached = repo.load({}, now=NOW + timedelta(minutes=5), cached_only=True)
    assert [deal["lowest_price"] for deal in cached.deals] == ["1.99", "2.24", ""]


def test_store_names_replace_the_numbered_fallback():
    deal, = normalize_deals([row(storeID="25")], scope="all", store_names={"25": "Epic Games Store"})
    assert deal["store_name"] == "Epic Games Store"
    other, = normalize_deals([row(storeID="31")], scope="all")
    assert other["store_name"] == "商店 31"


@pytest.mark.parametrize(("sale", "low", "mark"), [
    ("1.99", "1.99", ("low", "")), ("1.89", "1.99", ("low", "")), ("2.09", "1.99", ("near", "")),
    ("2.99", "2.24", ("above", "2.24")), ("1.99", "0.00", ("free", "")), ("1.99", "", ("", "")),
])
def test_low_mark_compares_the_sale_with_the_all_time_low(sale, low, mark):
    assert low_mark({"sale_price": sale, "lowest_price": low}) == mark


def test_failed_low_and_store_lookups_keep_the_offers_and_are_not_retried_at_once(tmp_path):
    http = StoreHTTP([row()], steam_items(), stores=RuntimeError("down"), games=RuntimeError("down"))
    repo = DealRepository(tmp_path, http=http)
    snapshot = repo.load({}, now=NOW)
    assert snapshot.state == "live" and len(snapshot.deals) == 1
    assert snapshot.deals[0]["lowest_price"] == "" and snapshot.deals[0]["store_name"] == "Steam"
    repo.load({}, now=NOW + timedelta(hours=2))
    # The store list waits six hours after a failure; the lows are asked with every offer refresh.
    assert [url for url, _ in http.calls].count(STORES_URL) == 1
    assert [url for url, _ in http.calls].count(GAMES_URL) == 2


def test_cache_from_before_reviews_and_lows_is_refetched_once(tmp_path):
    http = StoreHTTP([row(gameID="1")], steam_items())
    repo = DealRepository(tmp_path, http=http)
    repo.load({}, now=NOW)
    path = tmp_path / "deals-steam.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    del envelope["facts"]
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert repo.load({}, now=NOW + timedelta(minutes=5)).state == "live"
    assert repo.load({}, now=NOW + timedelta(minutes=10)).state == "fresh_cache"
    assert [url for url, _ in http.calls].count(API_URL) == 2
