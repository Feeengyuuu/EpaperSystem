from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded

from plugins.magazine_covers.historical_catalog import (
    CATALOG_MAX_BYTES,
    CATALOG_MAX_ITEMS,
    DiscoveryPage,
    HISTORICAL_CATEGORIES,
    IA_COVER_FILE_MAX_BYTES,
    PROVIDER_CATEGORY_QUERIES,
    ProviderRateLimited,
    ProviderSecurityError,
    GallicaProvider,
    WikimediaCommonsProvider,
    InternetArchiveProvider,
    LibraryOfCongressProvider,
    MagazineHistoricalCatalogStore,
    MagazineHistoricalCatalog,
    MagazineIssueCandidate,
)


class FixedJsonClient:
    def __init__(self, data, *, url="https://archive.org/advancedsearch.php"):
        self.data = data
        self.url = url
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return SimpleNamespace(status=200, data=self.data, headers={}, url=self.url)


class FixedTextClient:
    def __init__(self, data, *, url):
        self.data = data
        self.url = url
        self.calls = []

    def request_text(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return SimpleNamespace(status=200, data=self.data, headers={}, url=self.url)


class FixedStatusClient:
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return SimpleNamespace(
            status=self.status,
            data={},
            headers=self.headers,
            url="https://archive.org/advancedsearch.php",
        )


class RoutedJsonClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses[url]
        if callable(response):
            response = response()
        if isinstance(response, tuple):
            status, data, headers = response
        else:
            status, data, headers = 200, response, {}
        return SimpleNamespace(
            status=status,
            data=data,
            headers=headers,
            url=url,
        )


def _candidate(record_id, *, issue_title=None, attribution="Internet Archive"):
    return MagazineIssueCandidate(
        provider="internet_archive",
        source="Internet Archive / Magazine Rack",
        source_record_id=record_id,
        publication="Example Magazine",
        issue=f"Issue {record_id}",
        year=1930,
        issue_title=issue_title or f"Example Magazine {record_id}",
        issue_date="1930",
        category="general_history",
        temporal_class="historical",
        curation_tier="discovery",
        adult=False,
        cover_url=f"https://archive.org/download/{record_id}/page/n0_w600.jpg",
        fallback_cover_url=f"https://archive.org/services/img/{record_id}",
        record_url=f"https://archive.org/details/{record_id}",
        rights="Copyright status not supplied",
        rights_uri="",
        attribution=attribution,
        personal_use_only=True,
    )


def test_candidate_has_stable_identity_and_preserves_rights_provenance():
    candidate = MagazineIssueCandidate(
        provider=" Internet_Archive ",
        source=" Internet Archive / Magazine Rack ",
        source_record_id=" time-1936-05-11 ",
        publication=" Time ",
        issue="Vol. 27, No. 19",
        year=1936,
        issue_title=" Time, May 11 1936 ",
        issue_date="1936-05-11",
        category=" NEWS_POLITICS ",
        temporal_class="historical",
        curation_tier="featured",
        adult=False,
        cover_url=("https://archive.org/download/time-1936-05-11/page/n0_w600.jpg"),
        fallback_cover_url="https://archive.org/services/img/time-1936-05-11",
        record_url="https://archive.org/details/time-1936-05-11",
        rights="Copyright status not supplied",
        rights_uri="",
        attribution="Time; preserved by Internet Archive",
        personal_use_only=True,
    )

    assert candidate.provider == "internet_archive"
    assert candidate.source == "Internet Archive / Magazine Rack"
    assert candidate.category == "news_politics"
    assert candidate.publication == "Time"
    assert candidate.cover_id == ("mc1_eaeb289d3ea399a5cf832331d74b1278317fbc84cd7c15ffeb320d8b7c132d5a")
    assert (
        replace(
            candidate,
            cover_url="https://archive.org/services/img/time-1936-05-11?updated=1",
        ).cover_id
        == candidate.cover_id
    )
    assert candidate.rights == "Copyright status not supplied"
    assert candidate.attribution == "Time; preserved by Internet Archive"
    assert MagazineIssueCandidate.from_dict(candidate.to_dict()) == candidate
    assert replace(candidate, category="unexpected").category == "general_history"

    with pytest.raises(ValueError, match="HTTPS"):
        replace(candidate, cover_url="http://archive.org/services/img/example")
    with pytest.raises(ValueError, match="provider allowlist"):
        replace(candidate, cover_url="https://evil.example/cover.jpg")


def test_catalog_store_is_atomic_deduplicated_and_hard_bounded(tmp_path):
    assert CATALOG_MAX_ITEMS == 5000
    assert CATALOG_MAX_BYTES == 8 * 1024 * 1024
    path = tmp_path / "historical-catalog.json"
    store = MagazineHistoricalCatalogStore(path, max_items=3, max_bytes=2600)

    saved = store.save([_candidate(str(index)) for index in range(1, 5)])

    assert [item.source_record_id for item in saved] == ["2", "3", "4"]
    assert path.stat().st_size <= 2600
    assert store.load() == saved

    updated = replace(_candidate("3"), issue_title="Corrected third issue")
    merged = store.merge((updated, _candidate("5")))

    assert [item.source_record_id for item in merged] == ["4", "3", "5"]
    assert merged[1].issue_title == "Corrected third issue"
    assert path.stat().st_size <= 2600

    byte_path = tmp_path / "byte-bounded.json"
    byte_store = MagazineHistoricalCatalogStore(
        byte_path,
        max_items=10,
        max_bytes=2400,
    )
    byte_saved = byte_store.save([_candidate(str(index), attribution="A" * 900) for index in range(1, 5)])
    assert 0 < len(byte_saved) < 4
    assert byte_saved[-1].source_record_id == "4"
    assert byte_path.stat().st_size <= 2400
    assert byte_store.load() == byte_saved

    with pytest.raises(ValueError, match="max_bytes"):
        MagazineHistoricalCatalogStore(
            tmp_path / "too-large.json",
            max_bytes=CATALOG_MAX_BYTES + 1,
        )


def test_internet_archive_discovers_public_items_without_bypassing_restrictions():
    client = FixedJsonClient(
        {
            "response": {
                "numFound": 25,
                "start": 0,
                "docs": [
                    {
                        "identifier": "time-1936-05-11",
                        "title": "Time, May 11 1936",
                        "date": "1936-05-11",
                        "creator": "Time Inc.",
                        "volume": "27",
                        "issue": "19",
                        "rights": "Copyright status not supplied",
                        "collection": ["magazine_rack", "newsmagazines"],
                    },
                    {
                        "identifier": "restricted-item",
                        "title": "Restricted",
                        "access-restricted-item": "true",
                    },
                ],
            }
        }
    )
    provider = InternetArchiveProvider(
        http_client=client,
        personal_mode=True,
        metadata_enrichment_limit=0,
    )

    page = provider.discover("news_politics", page_size=10)

    assert tuple(PROVIDER_CATEGORY_QUERIES) == (
        "internet_archive",
        "wikimedia_commons",
        "library_of_congress",
        "gallica",
    )
    assert tuple(PROVIDER_CATEGORY_QUERIES["internet_archive"]) == HISTORICAL_CATEGORIES
    assert "TIME" in PROVIDER_CATEGORY_QUERIES["internet_archive"]["news_politics"]
    assert "Sports Illustrated" in (PROVIDER_CATEGORY_QUERIES["internet_archive"]["sports"])
    assert "Vogue" in (PROVIDER_CATEGORY_QUERIES["wikimedia_commons"]["fashion_culture"])
    assert "Rolling Stone" in (PROVIDER_CATEGORY_QUERIES["library_of_congress"]["entertainment_music"])
    assert "Playboy" in PROVIDER_CATEGORY_QUERIES["gallica"]["adult"]
    assert len(page.candidates) == 1
    candidate = page.candidates[0]
    assert candidate.source_record_id == "time-1936-05-11"
    assert candidate.issue == "Vol. 27, No. 19"
    assert candidate.year == 1936
    assert candidate.cover_url == ("https://archive.org/download/time-1936-05-11/page/n0_w600.jpg")
    assert candidate.fallback_cover_url == ("https://archive.org/services/img/time-1936-05-11")
    assert candidate.personal_use_only is True
    assert candidate.curation_tier == "featured"
    assert page.next_cursor == "2"

    method, url, kwargs = client.calls[0]
    assert (method, url) == ("GET", "https://archive.org/advancedsearch.php")
    assert "-access-restricted-item:true" in kwargs["params"]["q"]
    assert kwargs["allow_redirects"] is False
    assert kwargs["max_bytes"] <= 1024 * 1024
    assert max(kwargs["timeout"]) <= 12
    assert kwargs["headers"]["User-Agent"].startswith("InkyPi-MagazineCovers/")
    assert "github.com/Feeengyuuu/EpaperSystem" in kwargs["headers"]["User-Agent"]


def test_internet_archive_personal_mode_keeps_unknown_rights_but_public_mode_does_not():
    payload = {
        "response": {
            "numFound": 2,
            "docs": [
                {"identifier": "unknown-rights", "title": "Ordinary History"},
                {
                    "identifier": "open-rights",
                    "title": "Open Art Review",
                    "licenseurl": ("https://creativecommons.org/publicdomain/mark/1.0/"),
                },
            ],
        }
    }
    personal = InternetArchiveProvider(
        http_client=FixedJsonClient(payload),
        personal_mode=True,
        metadata_enrichment_limit=0,
    ).discover("adult")
    public = InternetArchiveProvider(
        http_client=FixedJsonClient(payload),
        personal_mode=False,
        metadata_enrichment_limit=0,
    ).discover("adult")

    assert [item.source_record_id for item in personal.candidates] == [
        "unknown-rights",
        "open-rights",
    ]
    assert [item.source_record_id for item in public.candidates] == ["open-rights"]
    assert all(item.adult and item.category == "adult" for item in personal.candidates)


def test_internet_archive_marks_documented_history_featured_and_detects_adult_titles():
    client = FixedJsonClient(
        {
            "response": {
                "numFound": 2,
                "docs": [
                    {
                        "identifier": "industrial-arts-1917",
                        "title": "Industrial Arts Magazine, 1917",
                        "date": "1917",
                        "creator": "Frederick James Bryant",
                    },
                    {
                        "identifier": "playboy-1968-04",
                        "title": "Playboy, April 1968",
                        "date": "1968-04",
                        "creator": "Playboy Enterprises",
                    },
                ],
            }
        }
    )

    page = InternetArchiveProvider(
        http_client=client,
        metadata_enrichment_limit=0,
    ).discover("general_history")

    by_id = {item.source_record_id: item for item in page.candidates}
    assert by_id["industrial-arts-1917"].curation_tier == "featured"
    assert by_id["playboy-1968-04"].adult is True
    assert by_id["playboy-1968-04"].category == "adult"


def test_internet_archive_enriches_a_bounded_featured_candidate_from_metadata_files():
    identifier = "time-1936-05-11"
    search_url = "https://archive.org/advancedsearch.php"
    metadata_url = f"https://archive.org/metadata/{identifier}"
    client = RoutedJsonClient(
        {
            search_url: {
                "response": {
                    "numFound": 20,
                    "docs": [
                        {
                            "identifier": identifier,
                            "title": "Time, May 11 1936",
                            "date": "1936-05-11",
                            "creator": "Time Inc.",
                        },
                        {
                            "identifier": "ordinary-1936",
                            "title": "Ordinary Weekly, 1936",
                        },
                    ],
                }
            },
            metadata_url: {
                "metadata": {
                    "identifier": identifier,
                    "title": "Time, May 11 1936",
                    "date": "1936-05-11",
                    "creator": "Time Inc.",
                    "rights": "No known restrictions on publication",
                    "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
                    "collection": ["magazine_rack", "newsmagazines"],
                },
                "files": [
                    {
                        "name": f"{identifier}_text.pdf",
                        "format": "Text PDF",
                        "size": "9000000",
                    },
                    {
                        "name": "front cover 1936.jpg",
                        "format": "JPEG",
                        "size": "812345",
                        "source": "original",
                    },
                    {
                        "name": "__ia_thumb.jpg",
                        "format": "Item Tile",
                        "size": "12345",
                    },
                ],
            },
        }
    )

    page = InternetArchiveProvider(
        http_client=client,
        metadata_enrichment_limit=1,
    ).discover("news_politics", page_size=2)

    assert page.next_cursor == "2"
    assert page.request_count == 2
    assert [call[1] for call in client.calls] == [search_url, metadata_url]
    by_id = {item.source_record_id: item for item in page.candidates}
    enriched = by_id[identifier]
    assert enriched.cover_url == (
        "https://archive.org/download/time-1936-05-11/front%20cover%201936.jpg"
    )
    assert enriched.fallback_cover_url == (
        "https://archive.org/services/img/time-1936-05-11"
    )
    assert enriched.rights == "No known restrictions on publication"
    assert enriched.rights_uri == (
        "https://creativecommons.org/publicdomain/mark/1.0/"
    )
    assert enriched.personal_use_only is False
    assert enriched.cover_id == _candidate(identifier).cover_id
    assert by_id["ordinary-1936"].cover_url.endswith("/page/n0_w600.jpg")


def test_internet_archive_metadata_rejects_restricted_and_ignores_bad_metadata():
    search_url = "https://archive.org/advancedsearch.php"
    restricted_url = "https://archive.org/metadata/restricted-after-search"
    malformed_url = "https://archive.org/metadata/malformed-metadata"
    client = RoutedJsonClient(
        {
            search_url: {
                "response": {
                    "numFound": 2,
                    "docs": [
                        {
                            "identifier": "restricted-after-search",
                            "title": "Time, Restricted Issue",
                        },
                        {
                            "identifier": "malformed-metadata",
                            "title": "Vogue, Metadata Fallback",
                        },
                    ],
                }
            },
            restricted_url: {
                "is_dark": True,
                "metadata": {
                    "identifier": "restricted-after-search",
                    "access-restricted-item": "true",
                },
                "files": [],
            },
            malformed_url: {
                "metadata": ["not", "an", "object"],
                "files": "not a file list",
            },
        }
    )

    page = InternetArchiveProvider(
        http_client=client,
        metadata_enrichment_limit=2,
    ).discover("general_history", page_size=2)

    assert [call[1] for call in client.calls] == [
        search_url,
        restricted_url,
        malformed_url,
    ]
    assert [item.source_record_id for item in page.candidates] == [
        "malformed-metadata"
    ]
    fallback = page.candidates[0]
    assert fallback.cover_url.endswith("/page/n0_w600.jpg")
    assert fallback.rights == "Not supplied by provider"


def test_internet_archive_metadata_never_selects_oversized_or_unsafe_files():
    identifier = "safe-fallback-1920"
    search_url = "https://archive.org/advancedsearch.php"
    metadata_url = f"https://archive.org/metadata/{identifier}"
    client = RoutedJsonClient(
        {
            search_url: {
                "response": {
                    "numFound": 1,
                    "docs": [
                        {
                            "identifier": identifier,
                            "title": "Historic Art Review, 1920",
                            "date": "1920",
                            "creator": "A. Artist",
                        }
                    ],
                }
            },
            metadata_url: {
                "metadata": {
                    "identifier": identifier,
                    "title": "Historic Art Review, 1920",
                },
                "files": [
                    {
                        "name": "cover.jpg",
                        "format": "JPEG",
                            "size": str(IA_COVER_FILE_MAX_BYTES + 1),
                    },
                    {
                        "name": "../private-cover.jpg",
                        "format": "JPEG",
                        "size": "1000",
                    },
                    {
                        "name": "private-cover.jpg",
                        "format": "JPEG",
                        "size": "1000",
                        "private": "true",
                    },
                ],
            },
        }
    )

    candidate = InternetArchiveProvider(
        http_client=client,
        metadata_enrichment_limit=1,
    ).discover("art_design", page_size=1).candidates[0]

    assert candidate.cover_url == (
        "https://archive.org/download/safe-fallback-1920/page/n0_w600.jpg"
    )
    assert candidate.fallback_cover_url == (
        "https://archive.org/services/img/safe-fallback-1920"
    )


def test_commons_normalizes_open_license_and_uses_bounded_continuation():
    client = FixedJsonClient(
        {
            "batchcomplete": True,
            "continue": {"gsroffset": 12, "continue": "-||"},
            "query": {
                "pages": [
                    {
                        "pageid": 101,
                        "title": "File:Modern Art Review, 1923.jpg",
                        "imageinfo": [
                            {
                                "thumburl": (
                                    "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/cover.jpg/960px-cover.jpg"
                                ),
                                "descriptionurl": (
                                    "https://commons.wikimedia.org/wiki/File:Modern_Art_Review,_1923.jpg"
                                ),
                                "extmetadata": {
                                    "ObjectName": {"value": "Modern Art Review, 1923"},
                                    "DateTimeOriginal": {"value": "1923"},
                                    "LicenseShortName": {"value": "Public domain"},
                                    "LicenseUrl": {"value": ("https://creativecommons.org/publicdomain/mark/1.0/")},
                                    "Artist": {"value": "<b>A. Artist</b>"},
                                    "Credit": {"value": "Example Museum"},
                                    "Assessments": {"value": "Featured picture"},
                                },
                            }
                        ],
                    }
                ]
            },
        },
        url="https://commons.wikimedia.org/w/api.php",
    )
    provider = WikimediaCommonsProvider(http_client=client)

    page = provider.discover("art_design", page_size=12)

    assert page.next_cursor == "12"
    assert len(page.candidates) == 1
    candidate = page.candidates[0]
    assert candidate.provider == "wikimedia_commons"
    assert candidate.source_record_id == "101"
    assert candidate.year == 1923
    assert candidate.rights == "Public domain"
    assert candidate.rights_uri == ("https://creativecommons.org/publicdomain/mark/1.0/")
    assert candidate.attribution == "A. Artist; Example Museum; Wikimedia Commons"
    assert candidate.personal_use_only is False
    assert candidate.curation_tier == "featured"

    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["generator"] == "search"
    assert kwargs["params"]["gsrnamespace"] == 6
    assert kwargs["params"]["gsrlimit"] == 12
    assert kwargs["params"]["gsrsearch"] == (PROVIDER_CATEGORY_QUERIES["wikimedia_commons"]["art_design"])

    provider.discover("art_design", cursor=page.next_cursor, page_size=12)
    _, _, second_kwargs = client.calls[1]
    assert second_kwargs["params"]["gsroffset"] == 12
    assert "gsrcontinue" not in second_kwargs["params"]


def test_commons_also_accepts_legacy_pages_mapping_and_gsrcontinue():
    client = FixedJsonClient(
        {
            "continue": {"gsrcontinue": "offset|next-cover", "continue": "gsr||"},
            "query": {
                "pages": {
                    "202": {
                        "pageid": 202,
                        "title": "File:Historic Review.jpg",
                        "imageinfo": [
                            {
                                "thumburl": "https://upload.wikimedia.org/historic.jpg",
                                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Historic_Review.jpg",
                                "extmetadata": {
                                    "LicenseShortName": {"value": "Public domain"},
                                    "Artist": {"value": "Archive artist"},
                                },
                            }
                        ],
                    }
                }
            },
        },
        url="https://commons.wikimedia.org/w/api.php",
    )

    page = WikimediaCommonsProvider(http_client=client).discover("general_history")

    assert page.next_cursor == "offset|next-cover"
    assert [candidate.source_record_id for candidate in page.candidates] == ["202"]


def test_library_of_congress_preserves_item_rights_and_official_image():
    client = FixedJsonClient(
        {
            "pagination": {
                "page": 1,
                "total": 60,
                "next": "https://www.loc.gov/search/?sp=2&fo=json",
            },
            "results": [
                {
                    "id": "https://www.loc.gov/item/loc-123/",
                    "title": "Championship Sports Weekly cover",
                    "date": "1927",
                    "partof": ["Popular Graphic Arts"],
                    "contributor": ["Jane Designer"],
                    "rights": ["No known restrictions on publication"],
                    "image_url": [
                        "https://tile.loc.gov/image-services/iiif/service:small/full/pct:25/0/default.jpg",
                        "https://tile.loc.gov/image-services/iiif/service:large/full/pct:100/0/default.jpg",
                    ],
                }
            ],
        },
        url="https://www.loc.gov/search/",
    )
    provider = LibraryOfCongressProvider(http_client=client)

    page = provider.discover("sports", page_size=20)

    assert page.next_cursor == "2"
    candidate = page.candidates[0]
    assert candidate.source_record_id == "loc-123"
    assert candidate.publication == "Popular Graphic Arts"
    assert candidate.cover_url.endswith("/full/pct:100/0/default.jpg")
    assert candidate.rights == "No known restrictions on publication"
    assert candidate.attribution == "Jane Designer; Library of Congress"
    assert candidate.personal_use_only is False

    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["q"] == (PROVIDER_CATEGORY_QUERIES["library_of_congress"]["sports"])
    assert kwargs["params"]["sp"] == 1
    assert kwargs["params"]["c"] == 20


def test_gallica_normalizes_sru_record_and_builds_bounded_image_service_urls():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <srw:searchRetrieveResponse
      xmlns:srw="http://www.loc.gov/zing/srw/"
      xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/"
      xmlns:dc="http://purl.org/dc/elements/1.1/">
      <srw:numberOfRecords>3</srw:numberOfRecords>
      <srw:records>
        <srw:record><srw:recordData><oai_dc:dc>
          <dc:identifier>https://gallica.bnf.fr/ark:/12148/bpt6k123</dc:identifier>
          <dc:title>La Vie Parisienne, 1924</dc:title>
          <dc:date>1924</dc:date>
          <dc:creator>George Barbier</dc:creator>
          <dc:publisher>Bibliotheque nationale de France</dc:publisher>
          <dc:rights>domaine public</dc:rights>
        </oai_dc:dc></srw:recordData></srw:record>
      </srw:records>
    </srw:searchRetrieveResponse>"""
    client = FixedTextClient(xml, url="https://gallica.bnf.fr/SRU")
    provider = GallicaProvider(http_client=client)

    page = provider.discover("art_design", page_size=1)

    assert page.next_cursor == "2"
    candidate = page.candidates[0]
    assert candidate.provider == "gallica"
    assert candidate.source_record_id == "ark:/12148/bpt6k123"
    assert candidate.publication == "La Vie Parisienne"
    assert candidate.cover_url == ("https://gallica.bnf.fr/ark:/12148/bpt6k123/f1.medres")
    assert candidate.fallback_cover_url == ("https://gallica.bnf.fr/ark:/12148/bpt6k123/f1.thumbnail")
    assert candidate.rights == "domaine public"
    assert candidate.attribution == ("George Barbier; Bibliotheque nationale de France; Gallica")
    assert candidate.curation_tier == "featured"

    _, _, kwargs = client.calls[0]
    assert kwargs["params"]["operation"] == "searchRetrieve"
    assert kwargs["params"]["query"] == (PROVIDER_CATEGORY_QUERIES["gallica"]["art_design"])
    assert kwargs["params"]["startRecord"] == 1
    assert kwargs["params"]["maximumRecords"] == 1
    assert kwargs["max_bytes"] <= 1024 * 1024


def test_catalog_refresh_keeps_last_good_data_and_isolates_provider_failures(tmp_path):
    class PartialProvider:
        provider = "internet_archive"

        def __init__(self):
            self.calls = []

        def discover(self, category, *, cursor=None, page_size=24, context=None):
            self.calls.append((category, cursor, page_size, context))
            if category == "sports":
                raise RuntimeError("fixture provider unavailable")
            return DiscoveryPage(
                provider=self.provider,
                category=category,
                candidates=(_candidate(f"new-{category}"),),
                next_cursor=None,
            )

    provider = PartialProvider()
    catalog = MagazineHistoricalCatalog(
        tmp_path / "catalog.json",
        providers=(provider,),
    )
    old = _candidate("old-last-good")
    catalog.save((old,))

    result = catalog.refresh(
        categories=("news_politics", "sports", "adult"),
        include_adult=False,
        page_size=7,
    )

    assert [call[:3] for call in provider.calls] == [
        ("news_politics", None, 7),
        ("sports", None, 7),
    ]
    assert result.request_count == 2
    assert result.added_count == 1
    assert tuple(result.errors) == ("internet_archive:sports",)
    assert [item.source_record_id for item in catalog.load()] == [
        "old-last-good",
        "new-news_politics",
    ]


def test_refresh_prioritizes_full_ia_coverage_then_gives_each_archive_a_turn(tmp_path):
    events = []

    class EmptyProvider:
        def __init__(self, provider):
            self.provider = provider

        def discover(self, category, *, cursor=None, page_size=24, context=None):
            events.append((self.provider, category, cursor))
            return DiscoveryPage(
                provider=self.provider,
                category=category,
                candidates=(),
                next_cursor=None,
            )

    catalog = MagazineHistoricalCatalog(
        tmp_path / "catalog.json",
        providers=tuple(
            EmptyProvider(provider)
            for provider in (
                "internet_archive",
                "wikimedia_commons",
                "library_of_congress",
                "gallica",
            )
        ),
    )

    result = catalog.refresh(include_adult=True, page_size=40)

    assert events[:8] == [("internet_archive", category, None) for category in HISTORICAL_CATEGORIES]
    assert events[8:11] == [
        ("wikimedia_commons", "art_design", None),
        ("library_of_congress", "art_design", None),
        ("gallica", "art_design", None),
    ]
    assert result.request_count == 32


def test_refresh_uses_available_second_pages_to_fill_a_thin_category(tmp_path):
    class PaginatedProvider:
        provider = "internet_archive"

        def __init__(self):
            self.cursors = []

        def discover(self, category, *, cursor=None, page_size=24, context=None):
            self.cursors.append(cursor)
            start = 0 if cursor is None else 5
            count = 5 if cursor is None else 20
            return DiscoveryPage(
                provider=self.provider,
                category=category,
                candidates=tuple(
                    replace(
                        _candidate(f"art-{index}"),
                        category="art_design",
                    )
                    for index in range(start, start + count)
                ),
                next_cursor="2" if cursor is None else None,
            )

    provider = PaginatedProvider()
    catalog = MagazineHistoricalCatalog(
        tmp_path / "catalog.json",
        providers=(provider,),
    )

    result = catalog.refresh(categories=("art_design",), page_size=40)

    assert provider.cursors == [None, "2"]
    assert result.request_count == 2
    assert len(result.candidates) == 25


def test_provider_network_boundary_rejects_redirects_pages_and_long_retry_after():
    redirected_client = FixedJsonClient(
        {"response": {"numFound": 0, "docs": []}},
        url="https://archive.org.attacker.example/advancedsearch.php",
    )
    provider = InternetArchiveProvider(http_client=redirected_client)
    with pytest.raises(ProviderSecurityError, match="allowlist"):
        provider.discover("general_history")

    with pytest.raises(ValueError, match="page_size"):
        provider.discover("general_history", page_size=51)
    with pytest.raises(ValueError, match="page range"):
        provider.discover("general_history", cursor="101")
    assert len(redirected_client.calls) == 1

    rate_limited_client = FixedStatusClient(429, {"Retry-After": "3600"})
    provider = InternetArchiveProvider(http_client=rate_limited_client)
    with pytest.raises(ProviderRateLimited) as raised:
        provider.discover("general_history")
    assert raised.value.retry_after_seconds == 6.0


def test_catalog_stops_a_rate_limited_provider_for_the_round_without_sleeping(tmp_path):
    rate_limited_client = FixedStatusClient(429, {"Retry-After": "5"})
    internet_archive = InternetArchiveProvider(
        http_client=rate_limited_client,
        metadata_enrichment_limit=0,
    )

    class AvailableProvider:
        provider = "wikimedia_commons"

        def __init__(self):
            self.calls = []

        def discover(self, category, *, cursor=None, page_size=24, context=None):
            self.calls.append((category, cursor))
            return DiscoveryPage(
                provider=self.provider,
                category=category,
                candidates=(),
                next_cursor=None,
            )

    available = AvailableProvider()
    catalog = MagazineHistoricalCatalog(
        tmp_path / "catalog.json",
        providers=(internet_archive, available),
    )

    result = catalog.refresh(
        categories=("art_design", "sports"),
        page_size=5,
    )

    assert len(rate_limited_client.calls) == 1
    assert available.calls == [("art_design", None), ("sports", None)]
    assert result.request_count == 3
    assert tuple(result.errors) == ("internet_archive:art_design",)

    with pytest.raises(ProviderRateLimited):
        internet_archive.discover("general_history")
    assert len(rate_limited_client.calls) == 1


def test_internet_archive_metadata_rate_limit_stops_before_another_search():
    search_url = "https://archive.org/advancedsearch.php"
    metadata_url = "https://archive.org/metadata/time-rate-limited"
    client = RoutedJsonClient(
        {
            search_url: {
                "response": {
                    "numFound": 1,
                    "docs": [
                        {
                            "identifier": "time-rate-limited",
                            "title": "Time, Metadata Rate Limit",
                        }
                    ],
                }
            },
            metadata_url: (429, {}, {"Retry-After": "4"}),
        }
    )
    provider = InternetArchiveProvider(
        http_client=client,
        metadata_enrichment_limit=1,
    )

    with pytest.raises(ProviderRateLimited) as raised:
        provider.discover("news_politics")
    assert raised.value.retry_after_seconds == 4.0
    assert [call[1] for call in client.calls] == [search_url, metadata_url]

    with pytest.raises(ProviderRateLimited):
        provider.discover("sports")
    assert [call[1] for call in client.calls] == [search_url, metadata_url]


def test_internet_archive_metadata_respects_the_shared_data_deadline():
    clock = [0.0]

    class DeadlineClient:
        def __init__(self):
            self.calls = []

        def request_json(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            assert kwargs["context"].deadline_monotonic <= 6.0
            clock[0] = 75.0
            return SimpleNamespace(
                status=200,
                data={
                    "response": {
                        "numFound": 1,
                        "docs": [
                            {
                                "identifier": "time-at-deadline",
                                "title": "Time, Deadline Issue",
                            }
                        ],
                    }
                },
                headers={},
                url=url,
            )

    client = DeadlineClient()
    provider = InternetArchiveProvider(
        http_client=client,
        metadata_enrichment_limit=1,
    )
    context = TaskContext.never_cancelled(
        deadline_monotonic=75.0,
        clock=lambda: clock[0],
    )

    with pytest.raises(TaskDeadlineExceeded):
        provider.discover("news_politics", context=context)
    assert [call[1] for call in client.calls] == [
        "https://archive.org/advancedsearch.php"
    ]


def test_refresh_commits_completed_pages_at_deadline_but_not_after_cancellation(tmp_path):
    class InterruptingProvider:
        provider = "internet_archive"

        def __init__(self, interruption):
            self.interruption = interruption

        def discover(self, category, *, cursor=None, page_size=24, context=None):
            if category == "sports":
                raise self.interruption("fixture interruption")
            return DiscoveryPage(
                provider=self.provider,
                category=category,
                candidates=(_candidate(f"completed-{category}"),),
                next_cursor=None,
            )

    deadline_catalog = MagazineHistoricalCatalog(
        tmp_path / "deadline.json",
        providers=(InterruptingProvider(TaskDeadlineExceeded),),
    )
    result = deadline_catalog.refresh(categories=("art_design", "sports"))

    assert result.deadline_exhausted is True
    assert [item.source_record_id for item in deadline_catalog.load()] == ["completed-art_design"]

    cancel_catalog = MagazineHistoricalCatalog(
        tmp_path / "cancel.json",
        providers=(InterruptingProvider(TaskCancelled),),
    )
    original = _candidate("original")
    cancel_catalog.save((original,))
    with pytest.raises(TaskCancelled):
        cancel_catalog.refresh(categories=("art_design", "sports"))
    assert cancel_catalog.load() == (original,)
