"""Daily Art's three further keyless collections: Japan Search's ukiyo-e, the Minneapolis Institute
of Art's Chinese works and the David Rumsey Map Collection's antique maps. The fixtures are cut
from the three services' answers of 2026-11-11."""

import random
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_art import daily_art as module  # noqa: E402
from plugins.daily_art.presentation_bank import provider_media_host_allowed  # noqa: E402
from runtime.long_task_executor import InstanceIdentity, bind_long_task_runtime  # noqa: E402
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded  # noqa: E402
from tests.test_daily_art import FakeDeviceConfig, make_plugin  # noqa: E402

COLBASE = "https://colbase.nich.go.jp/media/tnm/A-9115/image/slideshow_s/A-9115_E0046488.jpg"
JAPAN_SEARCH = {"hit": 193670, "from": 40, "list": [
    # A book about ukiyo-e: no picture, no rights statement.
    {"id": "bibnl-75045843", "common": {
        "access": "PUBLIC", "category": ["book"], "contentsAccess": "not_exist", "database": "bibnl",
        "contributor": ["編集制作: 座右宝刊行会", "集英社"], "id": "bibnl-75045843", "provider": "ndl",
        "linkUrl": "https://ndlsearch.ndl.go.jp/books/R100000002-I000001281934", "temporal": ["1974", "1974"],
        "title": "浮世絵大系"}},
    {"id": "cobas-46490", "common": {
        "access": "PUBLIC", "category": ["cultural", "art"], "contentsAccess": "internet", "contentsRightsType": "ccby",
        "contentsType": "image", "contentsUrl": [COLBASE],
        "contributor": ["三代歌川豊国 （1786-1864)筆", "By Utagawa Toyokuni III (1786-1864)", "東京国立博物館",
                        "Tokyo National Museum"],
        "database": "cobas", "id": "cobas-46490", "linkUrl": "https://colbase.nich.go.jp/collection_items/tnm/A-9115",
        "ownerOrg": "nich", "provider": "tnm", "temporal": ["江戸時代・19世紀", "Edo period, 19th century"],
        "thumbnailUrl": [COLBASE], "title": "浮世絵版画",
        "titleEn": "Actors Sawamura Sojuro I - IV in their Popular Roles"}},
    # A picture whose rights are not stated is not shown.
    {"id": "x-1", "common": {"contentsType": "image", "thumbnailUrl": ["https://www.dl.ndl.go.jp/api/iiif/1/R1/full/256,/0/default.jpg"],
                             "title": "無題", "database": "x", "id": "x-1"}},
]}
MIA = [{"_index": "objects2-with-ascii", "_id": "14672", "_score": 4.47, "_source": {
    "id": 14672, "title": "Snuff bottle", "medium": "Amber, jade", "dimension": "2 5/8 x 2in. (6.7 x 5.1cm)",
    "dated": "17th-19th century", "artist": "China", "life_date": None, "room": "Not on View", "culture": None,
    "country": "China", "rights_type": "Public Domain", "image": "valid", "image_width": 2073, "image_height": 2902,
    "highlights": 0, "department": "Asian Art"}},
    {"_id": "1", "_source": {"id": 1, "title": "No picture", "image": "invalid", "rights_type": "Public Domain"}},
    {"_id": "2", "_source": {"id": 2, "title": "In copyright", "image": "valid", "rights_type": "Copyright Protected"}},
    {"_id": "116725", "_source": {
        "id": 116725, "title": "Money Tree", "medium": "Bronze, glazed earthenware", "dated": "1st-2nd century",
        "artist": "Unknown", "room": "G215", "country": "China", "rights_type": "Public Domain", "image": "valid",
        "dimension": "53 1/2 x 22 1/4 in. (135.89 x 56.52 cm)", "highlights": 1}}]


def _map(identity, name, year, kind="Separate Map", author="United States. Post Office Department", number="18151001"):
    return {"displayName": name, "mediaType": "Image", "id": identity,
            "fieldValues": [{"Author": [author]}, {"Date": [year]}, {"Short Title": [name]}, {"Publisher": ["U.S. Post Office"]},
                            {"Type": [kind]}, {"Obj Height cm": ["80"]}, {"Obj Width cm": ["126"]},
                            {"State/Province": ["California", "Nevada"]}],
            "urlSize2": f"https://www.davidrumsey.com/rumsey/Size2/RUMSEY~8~1/229/{number}.jpg",
            "urlSize3": f"https://media.davidrumsey.com/MediaManager/srvr?mediafile=/Size3/RUMSEY~8~1/229/{number}.jpg",
            "urlSize4": f"https://media.davidrumsey.com/MediaManager/srvr?mediafile=/Size4/RUMSEY~8~1/229/{number}.jpg"}


RUMSEY = {"totalResults": 7507, "results": [
    _map("RUMSEY~8~1~384390~90150247", "A physician's view of San Francisco.", "1964", author="Caraway", number="14269001"),
    _map("RUMSEY~8~1~384391~90150246", "Text: A physician's view of San Francisco.", "1915", kind="Text Page"),
    _map("RUMSEY~8~1~384315~90150152", "Post route map of California and Nevada. Southern sheet", "1924"),
]}


def _plugin(tmp_path, monkeypatch, requests=None):
    plugin = make_plugin(tmp_path)
    payloads = {module.JAPAN_SEARCH_URL: JAPAN_SEARCH, module.MIA_SEARCH_URL: MIA, module.RUMSEY_SEARCH_URL: RUMSEY}

    def get_json(url, params, headers=None):
        if requests is not None:
            requests.append((url, dict(params)))
        return payloads[url]

    monkeypatch.setattr(plugin, "_get_json", get_json)
    return plugin


def test_japan_searchs_ukiyo_e_are_pictures_with_stated_open_rights(tmp_path, monkeypatch):
    requests = []
    plugin = _plugin(tmp_path, monkeypatch, requests)

    prints = plugin._fetch_japan_search_candidates("landscape", 6, random.Random(1))

    # One request, whatever the page's search term: the collection is its own subject.
    assert len(requests) == 1 and requests[0][1]["keyword"] == "浮世絵" and requests[0][1]["size"] <= 40
    assert len(prints) == 1
    work = prints[0]
    assert (work.source, work.source_label, work.artwork_id) == ("jpsearch", "Japan Search", "jpsearch:cobas-46490")
    assert work.title == "Actors Sawamura Sojuro I - IV in their Popular Roles"
    assert (work.artist, work.lifespan, work.date) == ("Utagawa Toyokuni III", "1786–1864", "Edo period, 19th century")
    assert (work.museum, work.rights, work.culture) == ("Tokyo National Museum", "CC BY", "Japan")
    assert work.page_url == "https://colbase.nich.go.jp/collection_items/tnm/A-9115"
    # ColBase's full picture, not its 227 px thumbnail; other hosts' thumbnails as they are.
    assert work.image_url == COLBASE.replace("/slideshow_s/", "/slideshow/")
    assert provider_media_host_allowed("jpsearch", "colbase.nich.go.jp")
    assert provider_media_host_allowed("jpsearch", "www.dl.ndl.go.jp")
    assert not provider_media_host_allowed("jpsearch", "example.com")


def test_minneapolis_gives_public_domain_chinese_works_with_a_picture(tmp_path, monkeypatch):
    requests = []
    plugin = _plugin(tmp_path, monkeypatch, requests)

    works = plugin._fetch_mia_candidates("landscape", 6, random.Random(1))

    assert len(requests) == 1 and requests[0][1] == {
        "q": 'image:valid rights_type:"Public Domain" country:China', "size": 6}
    assert sorted(work.title for work in works) == ["Money Tree", "Snuff bottle"]
    tree = next(work for work in works if work.title == "Money Tree")
    assert (tree.source, tree.source_label, tree.museum) == ("artsmia", "Minneapolis Institute of Art",
                                                             "Minneapolis Institute of Art")
    assert (tree.date, tree.medium, tree.rights, tree.culture) == ("1st-2nd century", "Bronze, glazed earthenware",
                                                                   "Public Domain", "China")
    # An unknown maker is no artist; the gallery it hangs in, its size and its highlight are kept.
    assert (tree.artist, tree.gallery, tree.dimensions, tree.highlight) == ("", "215", "135.89×56.52 cm", "1")
    # Its 800 px picture from the museum's own image host, which is numbered by the work's id.
    assert tree.image_url == f"https://{116725 % 7}.api.artsmia.org/800/116725.jpg"
    assert tree.page_url == "https://collections.artsmia.org/art/116725"
    bottle = next(work for work in works if work.title == "Snuff bottle")
    assert bottle.artist == "" and bottle.gallery == ""
    assert provider_media_host_allowed("artsmia", "3.api.artsmia.org")
    assert not provider_media_host_allowed("artsmia", "artsmia.org.example.com")


def test_rumseys_maps_are_antique_maps_not_text_pages_or_modern_ones(tmp_path, monkeypatch):
    requests = []
    plugin = _plugin(tmp_path, monkeypatch, requests)

    maps = plugin._fetch_rumsey_candidates("landscape", 6, random.Random(1))

    assert len(requests) == 1 and requests[0][1]["lc"] == "RUMSEY~8~1"
    assert requests[0][1]["q"] in module.RUMSEY_QUERIES and requests[0][1]["bs"] <= 30
    # The 1964 pictorial map is still in copyright and the text page is no map.
    assert [work.title for work in maps] == ["Post route map of California and Nevada. Southern sheet"]
    work = maps[0]
    assert (work.source, work.source_label) == ("rumsey", "David Rumsey Map Collection")
    assert (work.artist, work.date, work.medium) == ("United States. Post Office Department", "1924", "Separate Map")
    assert (work.dimensions, work.culture) == ("80×126 cm", "California")
    assert work.rights == "Public Domain · image CC BY-NC-SA, David Rumsey Map Collection"
    # A size near the page's frames, never the full scan.
    assert work.image_url == "https://media.davidrumsey.com/MediaManager/srvr?mediafile=/Size3/RUMSEY~8~1/229/18151001.jpg"
    assert work.page_url == "https://www.davidrumsey.com/luna/servlet/detail/RUMSEY~8~1~384315~90150152"
    assert provider_media_host_allowed("rumsey", "media.davidrumsey.com")
    assert not provider_media_host_allowed("rumsey", "example.com")


def test_the_three_collections_join_the_keyless_sources_and_the_days_pool(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch)
    device = FakeDeviceConfig()

    assert module.OPEN_SOURCES[-3:] == ("jpsearch", "artsmia", "rumsey")
    assert plugin._enabled_sources({"sourceMode": "open"}, device)[-3:] == ["jpsearch", "artsmia", "rumsey"]
    assert plugin._enabled_sources({"sourceMode": "rumsey"}, device) == ["rumsey"]
    assert plugin._enabled_sources({"sources": "ukiyo-e, mia, maps"}, device) == ["jpsearch", "artsmia", "rumsey"]
    for name in ("_fetch_met_candidates", "_fetch_artic_candidates", "_fetch_cleveland_candidates",
                 "_fetch_vam_candidates", "_fetch_smk_candidates"):
        monkeypatch.setattr(plugin, name, lambda *_args, **_kwargs: [])
    now = module.datetime(2026, 11, 11, 9, 0, tzinfo=module.timezone.utc)

    pool = plugin._candidate_pool({"sourceMode": "open"}, device, now)

    assert {work.source for work in pool} == {"jpsearch", "artsmia", "rumsey"}
    # The day's pick is stable: the same day gives the same pool in the same order.
    assert [work.artwork_id for work in plugin._candidate_pool({"sourceMode": "open"}, device, now)] == [
        work.artwork_id for work in pool]


def test_one_collection_failing_leaves_the_others(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path, monkeypatch)

    def broken(*_args, **_kwargs):
        raise OSError("down")

    monkeypatch.setattr(plugin, "_fetch_rumsey_candidates", broken)
    pool = plugin._candidate_pool({"sources": "jpsearch,artsmia,rumsey"}, FakeDeviceConfig(),
                                  module.datetime(2026, 11, 11, 9, 0, tzinfo=module.timezone.utc))
    assert {work.source for work in pool} == {"jpsearch", "artsmia"}
    # Answers that are not what the page expects give no works rather than an error.
    monkeypatch.setattr(plugin, "_get_json", lambda url, params, headers=None: {"error": "busy"})
    assert plugin._fetch_japan_search_candidates("x", 6, random.Random(1)) == []
    assert plugin._fetch_mia_candidates("x", 6, random.Random(1)) == []


@pytest.mark.parametrize("source", ["jpsearch", "artsmia", "rumsey"])
def test_collection_pool_uses_disk_cache_and_keeps_last_good_on_failure(tmp_path, monkeypatch, source):
    requests = []
    plugin = _plugin(tmp_path, monkeypatch, requests)
    settings = {"sources": source}
    now = module.datetime(2026, 11, 11, 9, 0, tzinfo=module.timezone.utc)
    first = plugin._candidate_pool(settings, FakeDeviceConfig(), now)
    assert first and len(requests) == 1

    # A separate renderer process can reuse the normalised pool without asking the provider.
    another = make_plugin(tmp_path)

    def unavailable(*_args, **_kwargs):
        requests.append("unavailable")
        raise OSError("collection unavailable")

    monkeypatch.setattr(another, "_get_json", unavailable)
    assert another._candidate_pool(settings, FakeDeviceConfig(), now + module.timedelta(hours=6)) == first
    assert len(requests) == 1
    assert another._candidate_pool(settings, FakeDeviceConfig(), now + module.timedelta(hours=25)) == first
    assert len(requests) == 2
    assert another._candidate_pool(settings, FakeDeviceConfig(), now + module.timedelta(days=8)) == []


@pytest.mark.parametrize("error", [TaskCancelled, TaskDeadlineExceeded])
def test_collection_cancellation_propagates_instead_of_using_stale_art(tmp_path, monkeypatch, error):
    plugin = _plugin(tmp_path, monkeypatch)
    settings = {"sources": "artsmia"}
    now = module.datetime(2026, 11, 11, 9, 0, tzinfo=module.timezone.utc)
    assert plugin._candidate_pool(settings, FakeDeviceConfig(), now)
    clock = [0.0]
    parent = TaskContext(threading.Event(), 120, lambda: clock[0])

    def canceled(*_args, **_kwargs):
        if error is TaskDeadlineExceeded:
            clock[0] = 121
        else:
            parent.cancel_event.set()
        raise error("stop collection refresh")

    monkeypatch.setattr(plugin, "_get_json", canceled)
    with bind_long_task_runtime(parent, InstanceIdentity("daily-art-test", 1, 1)), pytest.raises(error):
        plugin._candidate_pool(settings, FakeDeviceConfig(), now + module.timedelta(hours=25))


def test_new_collection_requests_have_a_small_response_budget(tmp_path, monkeypatch):
    requests = []

    class Client:
        def request_json(self, method, url, **kwargs):
            requests.append((url, kwargs))
            return type("Response", (), {"data": {}})()

    monkeypatch.setattr(module, "get_http_client", lambda: Client())
    plugin = make_plugin(tmp_path)
    for url in (module.JAPAN_SEARCH_URL, module.MIA_SEARCH_URL, module.RUMSEY_SEARCH_URL):
        plugin._get_json(url, {})
    assert all(0 < options.get("max_bytes", 0) <= 300 * 1024 for _, options in requests)
