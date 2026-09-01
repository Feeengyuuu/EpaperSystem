"""Bounded historical magazine discovery and catalog persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
from types import MappingProxyType
from pathlib import Path
import re
import time
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ElementTree

from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded
from utils.atomic_file import atomic_write_bytes
from utils.http_client import HttpStatusError, get_http_client


HISTORICAL_CATEGORIES = (
    "art_design",
    "sports",
    "news_politics",
    "fashion_culture",
    "science_nature",
    "entertainment_music",
    "adult",
    "general_history",
)
SUPPORTED_SOURCES = frozenset(
    {
        "internet_archive",
        "wikimedia_commons",
        "library_of_congress",
        "gallica",
    }
)
_COVER_ID_NAMESPACE = "magazine-cover-v1"
TEMPORAL_CLASSES = frozenset({"historical", "latest"})
CURATION_TIERS = frozenset({"featured", "discovery"})
CATALOG_MAX_ITEMS = 5000
CATALOG_MAX_BYTES = 8 * 1024 * 1024
CATALOG_VERSION = 1
_CANDIDATE_DOCUMENT_FIELDS = (
    "cover_id",
    "provider",
    "source",
    "source_record_id",
    "publication",
    "issue",
    "year",
    "issue_title",
    "issue_date",
    "category",
    "temporal_class",
    "curation_tier",
    "adult",
    "cover_url",
    "fallback_cover_url",
    "record_url",
    "rights",
    "rights_uri",
    "attribution",
    "personal_use_only",
)
PROVIDER_RESPONSE_MAX_BYTES = 1024 * 1024
PROVIDER_CONNECT_TIMEOUT_SECONDS = 3.0
PROVIDER_REQUEST_BUDGET_SECONDS = 6.0
PROVIDER_PAGE_SIZE_MAX = 50
PROVIDER_PAGE_MAX = 100
PROVIDER_CURSOR_MAX_CHARS = 512
PROVIDER_USER_AGENT = (
    "InkyPi-MagazineCovers/1.0 "
    "(+https://github.com/Feeengyuuu/EpaperSystem)"
)
CATALOG_TARGET_ITEMS = 240
CATEGORY_TARGET_ITEMS = 20
FEATURED_TARGET_ITEMS = 20
REFRESH_REQUEST_MAX = 48
IA_METADATA_ENRICH_MAX_PER_REFRESH = 8
IA_METADATA_ENRICH_MAX_PER_PAGE = 2
IA_COVER_FILE_MAX_BYTES = 16 * 1024 * 1024
_IA_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_IA_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
_IA_RESTRICTED_COLLECTIONS = frozenset(
    {
        "borrowablebooks",
        "inlibrary",
        "internetarchivebooks",
        "printdisabled",
    }
)
_FEATURED_PUBLICATIONS = frozenset(
    {
        "the atlantic",
        "the economist",
        "the new yorker",
        "harper's bazaar",
        "life",
        "national geographic",
        "newsweek",
        "playboy",
        "rolling stone",
        "sports illustrated",
        "time",
        "vanity fair",
        "vogue",
    }
)
_CULTURAL_FEATURED_CATEGORIES = frozenset(
    {
        "art_design",
        "fashion_culture",
        "science_nature",
        "entertainment_music",
    }
)
_ADULT_TITLE_RE = re.compile(
    r"(?:^|\b)(?:playboy|penthouse|hustler|erotica|erotic|pornography|pornographic|pin[ -]?up)(?:\b|$)",
    re.IGNORECASE,
)
_PROVIDER_ASSET_RULES = MappingProxyType(
    {
        "internet_archive": (
            frozenset({"archive.org"}),
            frozenset({"archive.org"}),
        ),
        "wikimedia_commons": (
            frozenset({"commons.wikimedia.org", "upload.wikimedia.org"}),
            frozenset(),
        ),
        "library_of_congress": (
            frozenset({"www.loc.gov"}),
            frozenset({"loc.gov"}),
        ),
        "gallica": (
            frozenset({"gallica.bnf.fr"}),
            frozenset(),
        ),
    }
)


def _query_map(values):
    return MappingProxyType(dict(zip(HISTORICAL_CATEGORIES, values, strict=True)))


PROVIDER_CATEGORY_QUERIES = MappingProxyType(
    {
        "internet_archive": _query_map(
            (
                'collection:magazine_rack AND mediatype:texts AND -access-restricted-item:true AND (title:(Vogue OR "Vanity Fair" OR art OR design) OR subject:(art OR design))',
                'collection:magazine_rack AND mediatype:texts AND -access-restricted-item:true AND (title:("Sports Illustrated" OR sport OR football OR baseball OR basketball OR hockey OR soccer) OR subject:(sport OR football OR baseball OR basketball OR hockey OR soccer))',
                'collection:magazine_rack AND mediatype:texts AND -access-restricted-item:true AND (title:(TIME OR Newsweek OR "The Economist" OR news OR politics OR weekly) OR subject:(news OR politics OR current-affairs))',
                'collection:magazine_rack AND mediatype:texts AND -access-restricted-item:true AND (title:(Vogue OR "Harper\'s Bazaar" OR fashion OR culture) OR subject:(fashion OR culture))',
                "collection:magazine_rack AND mediatype:texts AND -access-restricted-item:true AND (title:(science OR nature) OR subject:(science OR nature))",
                'collection:magazine_rack AND mediatype:texts AND -access-restricted-item:true AND (title:("Rolling Stone" OR entertainment OR music OR film) OR subject:(entertainment OR music OR film))',
                "collection:magazine_rack AND mediatype:texts AND -access-restricted-item:true AND (title:(Playboy OR Penthouse OR adult OR erotica OR pinup) OR subject:(adult OR erotica OR pinup))",
                "collection:magazine_rack AND mediatype:texts AND -access-restricted-item:true",
            )
        ),
        "wikimedia_commons": _query_map(
            (
                'deepcategory:"Magazine covers" (Vogue OR "Vanity Fair" OR art OR design)',
                'deepcategory:"Magazine covers" ("Sports Illustrated" OR sports)',
                'deepcategory:"Magazine covers" (TIME OR Newsweek OR news OR politics)',
                'deepcategory:"Magazine covers" (Vogue OR "Harper\'s Bazaar" OR fashion OR culture)',
                'deepcategory:"Magazine covers" (science OR nature)',
                'deepcategory:"Magazine covers" ("Rolling Stone" OR entertainment OR music OR film)',
                'deepcategory:"Magazine covers" (Playboy OR Penthouse OR adult OR erotica OR pin-up)',
                'deepcategory:"Magazine covers"',
            )
        ),
        "library_of_congress": _query_map(
            (
                '(Vogue OR "Vanity Fair" OR art OR design) magazine cover',
                '("Sports Illustrated" OR sports) magazine cover',
                '(TIME OR Newsweek OR "The Economist" OR news OR politics) magazine cover',
                '(Vogue OR "Harper\'s Bazaar" OR fashion OR culture) magazine cover',
                "(science OR nature) magazine cover",
                '("Rolling Stone" OR entertainment OR music) magazine cover',
                "(Playboy OR Penthouse OR pin-up) magazine cover",
                '(LIFE OR "National Geographic" OR "The New Yorker" OR magazine) cover',
            )
        ),
        "gallica": _query_map(
            (
                '(gallica all "La Vie Parisienne" or gallica all "Vogue" or gallica all "revue art" or gallica all "design")',
                '(gallica all "Sports Illustrated" or gallica all "revue sport")',
                '(gallica all "TIME" or gallica all "Newsweek" or gallica all "L Illustration" or gallica all "actualites politique")',
                '(gallica all "Vogue" or gallica all "mode" or gallica all "culture")',
                '(gallica all "revue science" or gallica all "nature")',
                '(gallica all "Rolling Stone" or gallica all "revue musique" or gallica all "cinema")',
                '(gallica all "Playboy" or gallica all "Penthouse" or gallica all "revue erotique" or gallica all "pin-up")',
                '(gallica all "LIFE" or gallica all "National Geographic" or gallica all "revue magazine")',
            )
        ),
    }
)


def _clean_required_text(value, *, field_name, max_chars=1024):
    if type(value) is not str:
        raise ValueError(f"{field_name} must be text")
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    if len(cleaned) > max_chars:
        raise ValueError(f"{field_name} is too long")
    return cleaned


def _clean_optional_text(value, *, field_name, max_chars=4096):
    if value is None:
        return ""
    if type(value) is not str:
        raise ValueError(f"{field_name} must be text")
    cleaned = " ".join(value.split())
    if len(cleaned) > max_chars:
        raise ValueError(f"{field_name} is too long")
    return cleaned


def _validated_https_url(value, *, field_name):
    cleaned = _clean_required_text(value, field_name=field_name, max_chars=4096)
    parsed = urlsplit(cleaned)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{field_name} must be a valid HTTPS URL") from error
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        raise ValueError(f"{field_name} must be an HTTPS URL on the default port")
    return cleaned


def _validated_provider_asset_url(value, *, provider, field_name):
    url = _validated_https_url(value, field_name=field_name)
    hostname = str(urlsplit(url).hostname or "").rstrip(".").lower()
    exact_hosts, parent_domains = _PROVIDER_ASSET_RULES[provider]
    allowed = hostname in exact_hosts or any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in parent_domains
    )
    if not allowed:
        raise ValueError(f"{field_name} is outside the provider allowlist")
    return url


@dataclass(frozen=True)
class MagazineIssueCandidate:
    """One normalized cover whose identity survives mutable URL metadata."""

    provider: str
    source: str
    source_record_id: str
    publication: str
    issue: str
    year: int | None
    issue_title: str
    issue_date: str
    category: str
    temporal_class: str
    curation_tier: str
    adult: bool
    cover_url: str
    fallback_cover_url: str
    record_url: str
    rights: str
    rights_uri: str
    attribution: str
    personal_use_only: bool
    cover_id: str = field(init=False)

    def __post_init__(self):
        provider = _clean_required_text(
            self.provider,
            field_name="provider",
            max_chars=64,
        ).lower()
        category = _clean_required_text(
            self.category,
            field_name="category",
            max_chars=32,
        ).lower()
        if provider not in SUPPORTED_SOURCES:
            raise ValueError("provider is not supported")
        if category not in HISTORICAL_CATEGORIES:
            category = "general_history"
        source_record_id = _clean_required_text(
            self.source_record_id,
            field_name="source_record_id",
            max_chars=512,
        )
        if type(self.personal_use_only) is not bool or type(self.adult) is not bool:
            raise ValueError("personal_use_only must be a boolean")
        if self.year is not None and (type(self.year) is not int or not 1000 <= self.year <= 3000):
            raise ValueError("year must be a plausible integer or null")
        temporal_class = _clean_required_text(
            self.temporal_class,
            field_name="temporal_class",
            max_chars=32,
        ).lower()
        if temporal_class not in TEMPORAL_CLASSES:
            raise ValueError("temporal_class is not supported")
        curation_tier = _clean_required_text(
            self.curation_tier,
            field_name="curation_tier",
            max_chars=32,
        ).lower()
        if curation_tier not in CURATION_TIERS:
            raise ValueError("curation_tier is not supported")
        adult = self.adult or category == "adult"
        if adult:
            category = "adult"

        normalized = {
            "provider": provider,
            "source": _clean_required_text(
                self.source,
                field_name="source",
                max_chars=512,
            ),
            "source_record_id": source_record_id,
            "publication": _clean_required_text(
                self.publication,
                field_name="publication",
                max_chars=512,
            ),
            "issue": _clean_optional_text(
                self.issue,
                field_name="issue",
                max_chars=256,
            ),
            "issue_title": _clean_required_text(
                self.issue_title,
                field_name="issue_title",
                max_chars=1024,
            ),
            "issue_date": _clean_optional_text(
                self.issue_date,
                field_name="issue_date",
                max_chars=128,
            ),
            "category": category,
            "temporal_class": temporal_class,
            "curation_tier": curation_tier,
            "adult": adult,
            "cover_url": _validated_https_url(
                self.cover_url,
                field_name="cover_url",
            ),
            "fallback_cover_url": _clean_optional_text(
                self.fallback_cover_url,
                field_name="fallback_cover_url",
            ),
            "record_url": _validated_https_url(
                self.record_url,
                field_name="record_url",
            ),
            "rights": _clean_optional_text(
                self.rights,
                field_name="rights",
            ),
            "rights_uri": _clean_optional_text(
                self.rights_uri,
                field_name="rights_uri",
            ),
            "attribution": _clean_required_text(
                self.attribution,
                field_name="attribution",
                max_chars=4096,
            ),
        }
        if normalized["fallback_cover_url"]:
            normalized["fallback_cover_url"] = _validated_https_url(
                normalized["fallback_cover_url"],
                field_name="fallback_cover_url",
            )
        for field_name in ("cover_url", "fallback_cover_url", "record_url"):
            if normalized[field_name]:
                normalized[field_name] = _validated_provider_asset_url(
                    normalized[field_name],
                    provider=provider,
                    field_name=field_name,
                )
        if normalized["rights_uri"]:
            normalized["rights_uri"] = _validated_https_url(
                normalized["rights_uri"],
                field_name="rights_uri",
            )
        for name, value in normalized.items():
            object.__setattr__(self, name, value)
        identity = "\0".join((_COVER_ID_NAMESPACE, provider, source_record_id))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        object.__setattr__(self, "cover_id", f"mc1_{digest}")

    def to_document(self):
        return {name: getattr(self, name) for name in _CANDIDATE_DOCUMENT_FIELDS}

    def to_dict(self):
        return self.to_document()

    @classmethod
    def from_document(cls, document):
        if type(document) is not dict or frozenset(document) != frozenset(_CANDIDATE_DOCUMENT_FIELDS):
            raise ValueError("historical magazine candidate fields are invalid")
        expected_cover_id = document.get("cover_id")
        values = {name: document[name] for name in _CANDIDATE_DOCUMENT_FIELDS[1:]}
        candidate = cls(**values)
        if expected_cover_id != candidate.cover_id:
            raise ValueError("historical magazine candidate identity is invalid")
        return candidate

    @classmethod
    def from_dict(cls, document):
        return cls.from_document(document)


@dataclass(frozen=True)
class DiscoveryPage:
    provider: str
    category: str
    candidates: tuple[MagazineIssueCandidate, ...]
    next_cursor: str | None
    request_count: int = 1


class HistoricalProviderError(RuntimeError):
    pass


class ProviderSecurityError(HistoricalProviderError):
    pass


class ProviderRateLimited(HistoricalProviderError):
    def __init__(self, provider, retry_after_seconds=None):
        self.provider = str(provider)
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{self.provider} historical discovery was rate limited")


def _normalized_category(value):
    if type(value) is not str:
        return "general_history"
    category = value.strip().lower()
    return category if category in HISTORICAL_CATEGORIES else "general_history"


def _validated_page_size(value):
    if type(value) is not int or not 1 <= value <= PROVIDER_PAGE_SIZE_MAX:
        raise ValueError(f"page_size must be between 1 and {PROVIDER_PAGE_SIZE_MAX}")
    return value


def _numeric_cursor(value, *, default=1, maximum=PROVIDER_PAGE_MAX):
    if value is None:
        return default
    if type(value) is not str or not value.isascii() or not value.isdigit():
        raise ValueError("provider cursor is invalid")
    parsed = int(value)
    if not 1 <= parsed <= maximum:
        raise ValueError("provider cursor is outside the allowed page range")
    return parsed


def _opaque_cursor(value):
    if value is None:
        return None
    if (
        type(value) is not str
        or not value
        or len(value) > PROVIDER_CURSOR_MAX_CHARS
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("provider cursor is invalid")
    return value


def _bounded_context(context):
    if context is None:
        return None
    context.raise_if_cancelled()
    now = context.clock()
    return TaskContext(
        cancel_event=context.cancel_event,
        deadline_monotonic=min(
            context.deadline_monotonic,
            now + PROVIDER_REQUEST_BUDGET_SECONDS,
        ),
        clock=context.clock,
    )


def _retry_after_seconds(headers):
    if not hasattr(headers, "items"):
        return None
    value = None
    for key, candidate in headers.items():
        if str(key).lower() == "retry-after":
            value = str(candidate).strip()
            break
    if not value:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    return min(PROVIDER_REQUEST_BUDGET_SECONDS, max(0.0, delay))


def _validated_provider_url(url, *, exact_hosts=(), parent_domains=()):
    parsed = urlsplit(str(url or ""))
    try:
        port = parsed.port
    except ValueError as error:
        raise ProviderSecurityError("provider URL authority is invalid") from error
    hostname = str(parsed.hostname or "").rstrip(".").lower()
    allowed = hostname in exact_hosts or any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in parent_domains
    )
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not allowed
    ):
        raise ProviderSecurityError("provider URL is outside its HTTPS allowlist")
    return str(url)


def _first_text(value, default=""):
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _first_text(item)
            if text:
                return text
        return default
    if value is None or isinstance(value, (dict, bool)):
        return default
    text = " ".join(str(value).split())
    return text or default


def _plain_metadata_text(value):
    text = _first_text(value)
    text = html.unescape(re.sub(r"<[^>]*>", " ", text))
    return " ".join(text.split())


def _metadata_value(metadata, key):
    if type(metadata) is not dict:
        return ""
    value = metadata.get(key)
    if type(value) is dict:
        value = value.get("value")
    return _plain_metadata_text(value)


def _truthy_provider_flag(value):
    if isinstance(value, (list, tuple)):
        return any(_truthy_provider_flag(item) for item in value)
    if value is True:
        return True
    return str(value or "").strip().lower() in {"1", "true", "yes", "restricted"}


def _year_from_value(value):
    match = re.search(r"(?<!\d)(1[5-9]\d{2}|20\d{2}|2100)(?!\d)", _first_text(value))
    return int(match.group(1)) if match else None


def _temporal_class(year):
    current_year = datetime.now(timezone.utc).year
    return "latest" if year is not None and year >= current_year - 1 else "historical"


def _curation_tier(publication, title="", featured_signal=""):
    publication_key = re.sub(r"[^a-z0-9']+", " ", str(publication).lower()).strip()
    title_key = re.sub(r"[^a-z0-9']+", " ", str(title).lower()).strip()
    for known_name in _FEATURED_PUBLICATIONS:
        if publication_key == known_name or publication_key.startswith(f"{known_name} "):
            return "featured"
        if title_key == known_name or title_key.startswith(f"{known_name} "):
            return "featured"
    signal = str(featured_signal or "").lower()
    if any(marker in signal for marker in ("featured picture", "quality image", "valued image", "highlight")):
        return "featured"
    return "discovery"


def _looks_adult(*values):
    return any(_ADULT_TITLE_RE.search(_first_text(value)) is not None for value in values)


def _named_creator(value, *, generic):
    creator = _first_text(value)
    return bool(creator and creator.casefold() != generic.casefold())


def _historically_documented_tier(
    publication,
    title,
    *,
    category,
    year,
    creator,
    generic_creator,
    rights_are_open,
    featured_signal="",
):
    tier = _curation_tier(publication, title, featured_signal)
    if tier == "featured":
        return tier
    has_named_creator = _named_creator(creator, generic=generic_creator)
    if year is not None and year < 1950 and has_named_creator:
        return "featured"
    if rights_are_open and category in _CULTURAL_FEATURED_CATEGORIES and has_named_creator:
        return "featured"
    return "discovery"


def _issue_label(document):
    volume = _first_text(document.get("volume"))
    issue = _first_text(document.get("issue"))
    if volume and issue:
        return f"Vol. {volume}, No. {issue}"
    if issue:
        return f"No. {issue}"
    if volume:
        return f"Vol. {volume}"
    return ""


def _open_rights_uri(value):
    text = _first_text(value)
    if not text:
        return ""
    parsed = urlsplit(text)
    hostname = str(parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme.lower() not in {"http", "https"} or hostname not in {
        "creativecommons.org",
        "rightsstatements.org",
    }:
        return ""
    return parsed._replace(scheme="https").geturl()


def _rights_are_open(rights_uri, rights_text=""):
    uri = str(rights_uri or "").lower()
    text = str(rights_text or "").lower()
    return (
        "creativecommons.org/publicdomain/" in uri
        or "creativecommons.org/licenses/by/" in uri
        or "creativecommons.org/licenses/by-sa/" in uri
        or "public domain" in text
        or "domaine public" in text
        or "no known restrictions" in text
    )


class _ProviderBase:
    provider = ""
    endpoint = ""
    api_exact_hosts = frozenset()
    api_parent_domains = frozenset()

    def __init__(self, *, http_client=None):
        self.http_client = http_client if http_client is not None else get_http_client()
        self._retry_not_before = 0.0

    def begin_refresh(self):
        """Reset per-refresh budgets without clearing a server-requested backoff."""

    @staticmethod
    def _clock(_context):
        return time.monotonic()

    def _raise_if_backed_off(self, context):
        if context is not None:
            context.raise_if_cancelled()
        remaining = self._retry_not_before - self._clock(context)
        if remaining > 0:
            raise ProviderRateLimited(
                self.provider,
                min(PROVIDER_REQUEST_BUDGET_SECONDS, remaining),
            )

    def _rate_limited(self, *, headers=None, context=None):
        retry_after = _retry_after_seconds(headers or {})
        if retry_after is None:
            retry_after = PROVIDER_REQUEST_BUDGET_SECONDS
        self._retry_not_before = max(
            self._retry_not_before,
            self._clock(context) + retry_after,
        )
        return ProviderRateLimited(self.provider, retry_after)

    def _request_json(self, *, params, context, endpoint=None):
        endpoint = self.endpoint if endpoint is None else endpoint
        _validated_provider_url(
            endpoint,
            exact_hosts=self.api_exact_hosts,
            parent_domains=self.api_parent_domains,
        )
        self._raise_if_backed_off(context)
        try:
            result = self.http_client.request_json(
                "GET",
                endpoint,
                context=_bounded_context(context),
                max_bytes=PROVIDER_RESPONSE_MAX_BYTES,
                timeout=(
                    PROVIDER_CONNECT_TIMEOUT_SECONDS,
                    PROVIDER_REQUEST_BUDGET_SECONDS,
                ),
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": PROVIDER_USER_AGENT,
                },
                allow_redirects=False,
            )
        except HttpStatusError as error:
            if error.status == 429:
                raise self._rate_limited(context=context) from error
            raise HistoricalProviderError(str(error)) from error
        status = int(getattr(result, "status", 0))
        if status == 429:
            raise self._rate_limited(
                headers=getattr(result, "headers", {}),
                context=context,
            )
        if not 200 <= status < 300:
            raise HistoricalProviderError(f"{self.provider} historical discovery returned HTTP {status}")
        _validated_provider_url(
            getattr(result, "url", ""),
            exact_hosts=self.api_exact_hosts,
            parent_domains=self.api_parent_domains,
        )
        return result.data

    def _request_text(self, *, params, context):
        _validated_provider_url(
            self.endpoint,
            exact_hosts=self.api_exact_hosts,
            parent_domains=self.api_parent_domains,
        )
        self._raise_if_backed_off(context)
        try:
            result = self.http_client.request_text(
                "GET",
                self.endpoint,
                context=_bounded_context(context),
                max_bytes=PROVIDER_RESPONSE_MAX_BYTES,
                timeout=(
                    PROVIDER_CONNECT_TIMEOUT_SECONDS,
                    PROVIDER_REQUEST_BUDGET_SECONDS,
                ),
                params=params,
                headers={
                    "Accept": "application/xml, text/xml;q=0.9",
                    "User-Agent": PROVIDER_USER_AGENT,
                },
                allow_redirects=False,
            )
        except HttpStatusError as error:
            if error.status == 429:
                raise self._rate_limited(context=context) from error
            raise HistoricalProviderError(str(error)) from error
        status = int(getattr(result, "status", 0))
        if status == 429:
            raise self._rate_limited(
                headers=getattr(result, "headers", {}),
                context=context,
            )
        if not 200 <= status < 300:
            raise HistoricalProviderError(f"{self.provider} historical discovery returned HTTP {status}")
        _validated_provider_url(
            getattr(result, "url", ""),
            exact_hosts=self.api_exact_hosts,
            parent_domains=self.api_parent_domains,
        )
        return result.data


class InternetArchiveProvider(_ProviderBase):
    provider = "internet_archive"
    endpoint = "https://archive.org/advancedsearch.php"
    api_exact_hosts = frozenset({"archive.org"})

    def __init__(
        self,
        *,
        http_client=None,
        personal_mode=True,
        metadata_enrichment_limit=IA_METADATA_ENRICH_MAX_PER_REFRESH,
    ):
        super().__init__(http_client=http_client)
        if type(personal_mode) is not bool:
            raise ValueError("personal_mode must be a boolean")
        if (
            type(metadata_enrichment_limit) is not int
            or not 0 <= metadata_enrichment_limit <= IA_METADATA_ENRICH_MAX_PER_REFRESH
        ):
            raise ValueError(
                "metadata_enrichment_limit must be between 0 and "
                f"{IA_METADATA_ENRICH_MAX_PER_REFRESH}"
            )
        self.personal_mode = personal_mode
        self.metadata_enrichment_limit = metadata_enrichment_limit
        self._metadata_requests_remaining = metadata_enrichment_limit

    def begin_refresh(self):
        self._metadata_requests_remaining = self.metadata_enrichment_limit

    def discover(self, category, *, cursor=None, page_size=24, context=None):
        category = _normalized_category(category)
        page_size = _validated_page_size(page_size)
        page = _numeric_cursor(cursor)
        data = self._request_json(
            params={
                "q": PROVIDER_CATEGORY_QUERIES[self.provider][category],
                "fl[]": (
                    "identifier",
                    "title",
                    "date",
                    "creator",
                    "volume",
                    "issue",
                    "rights",
                    "licenseurl",
                    "collection",
                    "access-restricted-item",
                ),
                "rows": page_size,
                "page": page,
                "output": "json",
            },
            context=context,
        )
        response = data.get("response") if type(data) is dict else None
        if type(response) is not dict or type(response.get("docs")) is not list:
            raise HistoricalProviderError("Internet Archive response schema is invalid")
        normalized_documents = []
        for document in response["docs"]:
            candidate = self._normalize_document(document, category)
            if candidate is not None:
                normalized_documents.append((document, candidate))
        metadata_budget_before = self._metadata_requests_remaining
        candidates = self._enrich_bounded_candidates(
            normalized_documents,
            category=category,
            context=context,
        )
        metadata_request_count = (
            metadata_budget_before - self._metadata_requests_remaining
        )
        try:
            total = max(0, int(response.get("numFound", 0)))
        except (TypeError, ValueError):
            total = 0
        next_cursor = None
        if page < PROVIDER_PAGE_MAX and page * page_size < total:
            next_cursor = str(page + 1)
        return DiscoveryPage(
            provider=self.provider,
            category=category,
            candidates=tuple(candidates),
            next_cursor=next_cursor,
            request_count=1 + metadata_request_count,
        )

    def _enrich_bounded_candidates(self, normalized_documents, *, category, context):
        candidates = [candidate for _document, candidate in normalized_documents]
        available = min(
            self._metadata_requests_remaining,
            IA_METADATA_ENRICH_MAX_PER_PAGE,
        )
        if available <= 0:
            return candidates
        prioritized_indexes = sorted(
            range(len(normalized_documents)),
            key=lambda index: (
                normalized_documents[index][1].curation_tier != "featured",
                "access-restricted-item" in normalized_documents[index][0],
                index,
            ),
        )
        for index in prioritized_indexes[:available]:
            if context is not None:
                context.raise_if_cancelled()
            document, fallback = normalized_documents[index]
            self._metadata_requests_remaining -= 1
            try:
                enriched = self._enrich_from_metadata(
                    document,
                    fallback,
                    category=category,
                    context=context,
                )
            except (ProviderRateLimited, TaskCancelled, TaskDeadlineExceeded):
                raise
            except HistoricalProviderError:
                enriched = fallback
            candidates[index] = enriched
        return [candidate for candidate in candidates if candidate is not None]

    def _enrich_from_metadata(self, document, fallback, *, category, context):
        identifier = fallback.source_record_id
        encoded_identifier = quote(identifier, safe="._-")
        data = self._request_json(
            endpoint=f"https://archive.org/metadata/{encoded_identifier}",
            params={},
            context=context,
        )
        metadata = data.get("metadata") if type(data) is dict else None
        files = data.get("files") if type(data) is dict else None
        if type(metadata) is not dict or type(files) is not list:
            return fallback
        metadata_identifier = _first_text(metadata.get("identifier"))
        if metadata_identifier != identifier:
            return fallback
        if self._metadata_is_restricted(data, metadata):
            return None

        merged = dict(document)
        for field_name in (
            "title",
            "date",
            "creator",
            "volume",
            "issue",
            "rights",
            "licenseurl",
            "collection",
        ):
            if field_name in metadata and _first_text(metadata.get(field_name)):
                merged[field_name] = metadata[field_name]
        cover_file = self._select_public_cover_file(files)
        cover_url = None
        if cover_file is not None:
            encoded_file = quote(cover_file, safe="/._-()")
            cover_url = (
                f"https://archive.org/download/{encoded_identifier}/{encoded_file}"
            )
        return self._normalize_document(
            merged,
            category,
            cover_url=cover_url,
        )

    @staticmethod
    def _metadata_is_restricted(data, metadata):
        for source in (data, metadata):
            for key in (
                "access-restricted-item",
                "access_restricted_item",
                "is_dark",
                "private",
                "restricted",
            ):
                if _truthy_provider_flag(source.get(key)):
                    return True
        access = _first_text(metadata.get("access")).casefold()
        if access and any(
            marker in access
            for marker in ("borrow", "login", "private", "restricted")
        ):
            return True
        collections = metadata.get("collection")
        if not isinstance(collections, (list, tuple)):
            collections = (collections,)
        return any(
            _first_text(collection).casefold() in _IA_RESTRICTED_COLLECTIONS
            for collection in collections
        )

    @staticmethod
    def _select_public_cover_file(files):
        eligible = []
        for file_document in files[:2048]:
            if type(file_document) is not dict:
                continue
            if any(
                _truthy_provider_flag(file_document.get(key))
                for key in (
                    "access-restricted-item",
                    "private",
                    "restricted",
                )
            ):
                continue
            name = _first_text(file_document.get("name"))
            if not InternetArchiveProvider._safe_file_name(name):
                continue
            suffix = "." + name.rsplit(".", 1)[-1].casefold()
            if suffix not in _IA_IMAGE_EXTENSIONS:
                continue
            image_format = _first_text(file_document.get("format")).casefold()
            if image_format and not any(
                marker in image_format
                for marker in ("image", "item tile", "jpeg", "jpg", "png", "webp")
            ):
                continue
            try:
                size = int(_first_text(file_document.get("size")))
            except (TypeError, ValueError):
                continue
            if not 0 < size <= IA_COVER_FILE_MAX_BYTES:
                continue
            lower_name = name.casefold()
            if "back" in lower_name and "cover" in lower_name:
                continue
            if "front" in lower_name or "cover" in lower_name:
                rank = 0
            elif "__ia_thumb" in lower_name:
                rank = 1
            elif any(
                marker in lower_name
                for marker in (
                    "_0000.",
                    "_0001.",
                    "page_0000",
                    "page0000",
                    "page_0001",
                    "page0001",
                    "scan_0000",
                    "scan_0001",
                )
            ):
                rank = 2
            else:
                continue
            eligible.append((rank, -size, lower_name, name))
        return min(eligible)[-1] if eligible else None

    @staticmethod
    def _safe_file_name(name):
        if (
            not name
            or len(name) > 512
            or name.startswith(("/", "\\"))
            or "\\" in name
            or any(character in name for character in ("?", "#", "%"))
            or any(ord(character) < 32 for character in name)
        ):
            return False
        return all(part not in {"", ".", ".."} for part in name.split("/"))

    def _normalize_document(self, document, category, *, cover_url=None):
        if type(document) is not dict:
            return None
        if _truthy_provider_flag(document.get("access-restricted-item")):
            return None
        identifier = _first_text(document.get("identifier"))
        if _IA_IDENTIFIER_RE.fullmatch(identifier) is None:
            return None
        rights_uri = _open_rights_uri(document.get("licenseurl"))
        rights = _first_text(document.get("rights"), "Not supplied by provider")
        is_open = _rights_are_open(rights_uri, rights)
        if not self.personal_mode and not is_open:
            return None
        title = _first_text(document.get("title"), identifier)
        creator = _first_text(document.get("creator"), "Internet Archive contributor")
        issue_date = _first_text(document.get("date"))
        year = _year_from_value(issue_date)
        encoded_identifier = quote(identifier, safe="._-")
        collection = _first_text(document.get("collection"), "Magazine Rack")
        publication = title.split(",", 1)[0].strip() or title
        adult = category == "adult" or _looks_adult(publication, title)
        return MagazineIssueCandidate(
            provider=self.provider,
            source=f"Internet Archive / {collection}",
            source_record_id=identifier,
            publication=publication,
            issue=_issue_label(document),
            year=year,
            issue_title=title,
            issue_date=issue_date,
            category=category,
            temporal_class=_temporal_class(year),
            curation_tier=_historically_documented_tier(
                publication,
                title,
                category=category,
                year=year,
                creator=creator,
                generic_creator="Internet Archive contributor",
                rights_are_open=is_open,
            ),
            adult=adult,
            cover_url=(
                cover_url
                or f"https://archive.org/download/{encoded_identifier}/page/n0_w600.jpg"
            ),
            fallback_cover_url=(f"https://archive.org/services/img/{encoded_identifier}"),
            record_url=f"https://archive.org/details/{encoded_identifier}",
            rights=rights,
            rights_uri=rights_uri,
            attribution=f"{creator}; Internet Archive",
            personal_use_only=not is_open,
        )


class WikimediaCommonsProvider(_ProviderBase):
    provider = "wikimedia_commons"
    endpoint = "https://commons.wikimedia.org/w/api.php"
    api_exact_hosts = frozenset({"commons.wikimedia.org"})

    def discover(self, category, *, cursor=None, page_size=24, context=None):
        category = _normalized_category(category)
        page_size = _validated_page_size(page_size)
        cursor = _opaque_cursor(cursor)
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "generator": "search",
            "gsrsearch": PROVIDER_CATEGORY_QUERIES[self.provider][category],
            "gsrnamespace": 6,
            "gsrlimit": page_size,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 1200,
        }
        if cursor is not None:
            if cursor.isascii() and cursor.isdigit():
                offset = int(cursor)
                if not 1 <= offset <= CATALOG_MAX_ITEMS:
                    raise ValueError("provider cursor is outside the allowed offset range")
                params["gsroffset"] = offset
            else:
                params["gsrcontinue"] = cursor
        data = self._request_json(params=params, context=context)
        query = data.get("query") if type(data) is dict else None
        pages = query.get("pages") if type(query) is dict else None
        if pages is None:
            page_documents = ()
        elif type(pages) is dict:
            page_documents = pages.values()
        elif type(pages) is list:
            page_documents = pages
        else:
            raise HistoricalProviderError("Wikimedia Commons response schema is invalid")
        candidates = []
        for document in page_documents:
            candidate = self._normalize_document(document, category)
            if candidate is not None:
                candidates.append(candidate)
        continuation = data.get("continue") if type(data) is dict else None
        next_cursor = None
        if type(continuation) is dict:
            offset = continuation.get("gsroffset")
            if type(offset) is int and 1 <= offset <= CATALOG_MAX_ITEMS:
                next_cursor = str(offset)
            elif continuation.get("gsrcontinue") is not None:
                try:
                    next_cursor = _opaque_cursor(continuation.get("gsrcontinue"))
                except ValueError:
                    next_cursor = None
        return DiscoveryPage(
            provider=self.provider,
            category=category,
            candidates=tuple(candidates),
            next_cursor=next_cursor,
        )

    def _normalize_document(self, document, category):
        if type(document) is not dict:
            return None
        page_id = document.get("pageid")
        if type(page_id) is not int or page_id <= 0:
            return None
        image_info = document.get("imageinfo")
        if type(image_info) is not list or not image_info or type(image_info[0]) is not dict:
            return None
        image_info = image_info[0]
        cover_url = _first_text(image_info.get("thumburl") or image_info.get("url"))
        record_url = _first_text(image_info.get("descriptionurl"))
        try:
            _validated_provider_url(
                cover_url,
                exact_hosts={"upload.wikimedia.org"},
            )
            _validated_provider_url(
                record_url,
                exact_hosts={"commons.wikimedia.org"},
            )
        except ProviderSecurityError:
            return None
        metadata = image_info.get("extmetadata")
        title = _metadata_value(metadata, "ObjectName")
        if not title:
            title = _first_text(document.get("title"), f"Commons file {page_id}")
            title = re.sub(r"^File:", "", title, flags=re.IGNORECASE)
            title = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", title)
        issue_date = _metadata_value(metadata, "DateTimeOriginal") or _metadata_value(metadata, "DateTime")
        year = _year_from_value(issue_date or title)
        rights = (
            _metadata_value(metadata, "LicenseShortName")
            or _metadata_value(metadata, "UsageTerms")
            or "Not supplied by provider"
        )
        rights_uri = _open_rights_uri(_metadata_value(metadata, "LicenseUrl"))
        artist = _metadata_value(metadata, "Artist")
        credit = _metadata_value(metadata, "Credit")
        publication = title.split(",", 1)[0].strip() or title
        is_open = _rights_are_open(rights_uri, rights)
        attribution_parts = []
        for value in (artist, credit, "Wikimedia Commons"):
            if value and value not in attribution_parts:
                attribution_parts.append(value)
        return MagazineIssueCandidate(
            provider=self.provider,
            source="Wikimedia Commons",
            source_record_id=str(page_id),
            publication=publication,
            issue="",
            year=year,
            issue_title=title,
            issue_date=issue_date,
            category=category,
            temporal_class=_temporal_class(year),
            curation_tier=_historically_documented_tier(
                publication,
                title,
                category=category,
                year=year,
                creator=artist or credit,
                generic_creator="Wikimedia Commons contributor",
                rights_are_open=is_open,
                featured_signal=_metadata_value(metadata, "Assessments"),
            ),
            adult=category == "adult" or _looks_adult(publication, title),
            cover_url=cover_url,
            fallback_cover_url="",
            record_url=record_url,
            rights=rights,
            rights_uri=rights_uri,
            attribution="; ".join(attribution_parts),
            personal_use_only=not is_open,
        )


class LibraryOfCongressProvider(_ProviderBase):
    provider = "library_of_congress"
    endpoint = "https://www.loc.gov/search/"
    api_exact_hosts = frozenset({"www.loc.gov"})

    def discover(self, category, *, cursor=None, page_size=24, context=None):
        category = _normalized_category(category)
        page_size = _validated_page_size(page_size)
        page = _numeric_cursor(cursor)
        data = self._request_json(
            params={
                "q": PROVIDER_CATEGORY_QUERIES[self.provider][category],
                "fo": "json",
                "fa": "online-format:image",
                "c": page_size,
                "sp": page,
            },
            context=context,
        )
        results = data.get("results") if type(data) is dict else None
        if type(results) is not list:
            raise HistoricalProviderError("Library of Congress response schema is invalid")
        candidates = []
        for document in results:
            candidate = self._normalize_document(document, category)
            if candidate is not None:
                candidates.append(candidate)
        pagination = data.get("pagination") if type(data) is dict else None
        next_cursor = None
        if type(pagination) is dict:
            try:
                total = max(0, int(pagination.get("total", 0)))
            except (TypeError, ValueError):
                total = 0
            if page < PROVIDER_PAGE_MAX and (pagination.get("next") or page * page_size < total):
                next_cursor = str(page + 1)
        return DiscoveryPage(
            provider=self.provider,
            category=category,
            candidates=tuple(candidates),
            next_cursor=next_cursor,
        )

    def _normalize_document(self, document, category):
        if type(document) is not dict:
            return None
        record_url = _first_text(document.get("id"))
        try:
            _validated_provider_url(record_url, parent_domains={"loc.gov"})
        except ProviderSecurityError:
            return None
        path_parts = [part for part in urlsplit(record_url).path.split("/") if part]
        source_record_id = path_parts[-1] if path_parts else ""
        if not source_record_id:
            return None
        image_values = document.get("image_url")
        if type(image_values) is str:
            image_values = [image_values]
        if type(image_values) is not list:
            return None
        allowed_images = []
        for value in image_values:
            url = _first_text(value)
            try:
                _validated_provider_url(url, parent_domains={"loc.gov"})
            except ProviderSecurityError:
                continue
            allowed_images.append(url)
        if not allowed_images:
            return None
        title = _first_text(document.get("title"), source_record_id)
        publication = _first_text(document.get("partof"), title.split(",", 1)[0])
        issue_date = _first_text(document.get("date"))
        year = _year_from_value(issue_date or title)
        rights = (
            _first_text(document.get("rights"))
            or _first_text(document.get("rights_advisory"))
            or "Not supplied by provider"
        )
        contributor = _first_text(
            document.get("contributor"),
            "Library of Congress contributor",
        )
        is_open = _rights_are_open("", rights)
        fallback_cover_url = allowed_images[-2] if len(allowed_images) > 1 else ""
        return MagazineIssueCandidate(
            provider=self.provider,
            source=f"Library of Congress / {publication}",
            source_record_id=source_record_id,
            publication=publication,
            issue=_first_text(document.get("number")),
            year=year,
            issue_title=title,
            issue_date=issue_date,
            category=category,
            temporal_class=_temporal_class(year),
            curation_tier=_historically_documented_tier(
                publication,
                title,
                category=category,
                year=year,
                creator=contributor,
                generic_creator="Library of Congress contributor",
                rights_are_open=is_open,
            ),
            adult=category == "adult" or _looks_adult(publication, title),
            cover_url=allowed_images[-1],
            fallback_cover_url=fallback_cover_url,
            record_url=record_url,
            rights=rights,
            rights_uri="",
            attribution=f"{contributor}; Library of Congress",
            personal_use_only=not is_open,
        )


class GallicaProvider(_ProviderBase):
    provider = "gallica"
    endpoint = "https://gallica.bnf.fr/SRU"
    api_exact_hosts = frozenset({"gallica.bnf.fr"})
    _SRW_NAMESPACE = "http://www.loc.gov/zing/srw/"
    _ARK_RE = re.compile(r"^ark:/12148/[A-Za-z0-9._-]+$")

    def discover(self, category, *, cursor=None, page_size=24, context=None):
        category = _normalized_category(category)
        page_size = _validated_page_size(page_size)
        start_record = _numeric_cursor(cursor, maximum=CATALOG_MAX_ITEMS)
        payload = self._request_text(
            params={
                "operation": "searchRetrieve",
                "version": "1.2",
                "query": PROVIDER_CATEGORY_QUERIES[self.provider][category],
                "startRecord": start_record,
                "maximumRecords": page_size,
            },
            context=context,
        )
        if "<!DOCTYPE" in payload.upper() or "<!ENTITY" in payload.upper():
            raise HistoricalProviderError("Gallica XML declarations are not allowed")
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as error:
            raise HistoricalProviderError("Gallica response XML is invalid") from error
        total_text = root.findtext(f".//{{{self._SRW_NAMESPACE}}}numberOfRecords")
        try:
            total = max(0, int(total_text or 0))
        except ValueError:
            total = 0
        record_nodes = root.findall(f".//{{{self._SRW_NAMESPACE}}}recordData")
        candidates = []
        for record_node in record_nodes:
            candidate = self._normalize_record(record_node, category)
            if candidate is not None:
                candidates.append(candidate)
        consumed = len(record_nodes)
        next_start = start_record + consumed
        next_cursor = None
        if consumed and next_start <= CATALOG_MAX_ITEMS and next_start <= total:
            next_cursor = str(next_start)
        return DiscoveryPage(
            provider=self.provider,
            category=category,
            candidates=tuple(candidates),
            next_cursor=next_cursor,
        )

    def _normalize_record(self, record_node, category):
        fields = {}
        for element in record_node.iter():
            if not isinstance(element.tag, str) or not element.text:
                continue
            local_name = element.tag.rsplit("}", 1)[-1].lower()
            text = _plain_metadata_text(element.text)
            if text:
                fields.setdefault(local_name, []).append(text)
        identifiers = fields.get("identifier", ())
        record_url = ""
        source_record_id = ""
        for value in identifiers:
            try:
                _validated_provider_url(value, exact_hosts={"gallica.bnf.fr"})
            except ProviderSecurityError:
                continue
            parsed = urlsplit(value)
            candidate_id = parsed.path.lstrip("/").rstrip("/")
            if self._ARK_RE.fullmatch(candidate_id) is not None:
                source_record_id = candidate_id
                record_url = f"https://gallica.bnf.fr/{candidate_id}"
                break
        if not source_record_id:
            return None
        title = _first_text(fields.get("title"), source_record_id)
        issue_date = _first_text(fields.get("date"))
        year = _year_from_value(issue_date or title)
        creator = _first_text(fields.get("creator"), "Gallica contributor")
        publisher = _first_text(
            fields.get("publisher"),
            "Bibliotheque nationale de France",
        )
        rights = _first_text(fields.get("rights"), "Not supplied by provider")
        publication = title.split(",", 1)[0].strip() or title
        is_open = _rights_are_open("", rights)
        artistic_public_domain = (
            category == "art_design"
            and is_open
            and _named_creator(
                creator,
                generic="Gallica contributor",
            )
        )
        return MagazineIssueCandidate(
            provider=self.provider,
            source=f"Gallica / {publisher}",
            source_record_id=source_record_id,
            publication=publication,
            issue=_first_text(fields.get("relation")),
            year=year,
            issue_title=title,
            issue_date=issue_date,
            category=category,
            temporal_class=_temporal_class(year),
            curation_tier=_historically_documented_tier(
                publication,
                title,
                category=category,
                year=year,
                creator=creator,
                generic_creator="Gallica contributor",
                rights_are_open=is_open,
                featured_signal="highlight" if artistic_public_domain else "",
            ),
            adult=category == "adult" or _looks_adult(publication, title),
            cover_url=f"{record_url}/f1.medres",
            fallback_cover_url=f"{record_url}/f1.thumbnail",
            record_url=record_url,
            rights=rights,
            rights_uri="",
            attribution=f"{creator}; {publisher}; Gallica",
            personal_use_only=True,
        )


@dataclass(frozen=True)
class CatalogRefreshResult:
    candidates: tuple[MagazineIssueCandidate, ...]
    added_count: int
    request_count: int
    deadline_exhausted: bool
    errors: MappingProxyType


def _initial_refresh_requests(providers, categories):
    providers = tuple(providers)
    categories = tuple(categories)
    primary = next(
        (provider for provider in providers if provider.provider == "internet_archive"),
        None,
    )
    if primary is not None:
        for category in categories:
            yield primary, category
    secondary = tuple(provider for provider in providers if provider is not primary)
    if not categories:
        return
    for provider in secondary:
        yield provider, categories[0]
    for category in categories[1:]:
        for provider in secondary:
            yield provider, category


def _catalog_needs_refill(existing, discovered, categories):
    candidates = _deduplicated_candidates((*existing, *discovered))
    counts = {category: 0 for category in categories}
    featured_count = 0
    for candidate in candidates:
        if candidate.category in counts:
            counts[candidate.category] += 1
        if candidate.curation_tier == "featured":
            featured_count += 1
    return (
        len(candidates) < CATALOG_TARGET_ITEMS
        or any(count < CATEGORY_TARGET_ITEMS for count in counts.values())
        or featured_count < FEATURED_TARGET_ITEMS
    )


class MagazineHistoricalCatalog:
    """Single DATA-phase entry point for discovery and last-good persistence."""

    def __init__(
        self,
        path,
        *,
        http_client=None,
        providers=None,
        personal_mode=True,
        max_items=CATALOG_MAX_ITEMS,
        max_bytes=CATALOG_MAX_BYTES,
    ):
        self.store = MagazineHistoricalCatalogStore(
            path,
            max_items=max_items,
            max_bytes=max_bytes,
        )
        if providers is None:
            providers = (
                InternetArchiveProvider(
                    http_client=http_client,
                    personal_mode=personal_mode,
                ),
                WikimediaCommonsProvider(http_client=http_client),
                LibraryOfCongressProvider(http_client=http_client),
                GallicaProvider(http_client=http_client),
            )
        providers = tuple(providers)
        provider_names = []
        for provider in providers:
            name = getattr(provider, "provider", None)
            if name not in SUPPORTED_SOURCES or not callable(getattr(provider, "discover", None)):
                raise ValueError("providers must implement a supported discovery source")
            provider_names.append(name)
        if len(set(provider_names)) != len(provider_names):
            raise ValueError("provider names must be unique")
        self.providers = providers

    @property
    def path(self):
        return self.store.path

    def load(self):
        return self.store.load()

    def save(self, candidates):
        return self.store.save(candidates)

    def refresh(
        self,
        *,
        categories=HISTORICAL_CATEGORIES,
        include_adult=False,
        page_size=24,
        context=None,
    ):
        if type(include_adult) is not bool:
            raise ValueError("include_adult must be a boolean")
        page_size = _validated_page_size(page_size)
        normalized_categories = []
        for value in categories:
            category = _normalized_category(value)
            if category == "adult" and not include_adult:
                continue
            if category not in normalized_categories:
                normalized_categories.append(category)

        existing = self.load()
        existing_ids = {candidate.cover_id for candidate in existing}
        discovered = []
        errors = {}
        request_count = 0
        deadline_exhausted = False
        continuations = []
        seen_continuations = set()
        rate_limited_providers = set()

        for provider in self.providers:
            begin_refresh = getattr(provider, "begin_refresh", None)
            if callable(begin_refresh):
                begin_refresh()

        def request_page(provider, category, cursor):
            nonlocal deadline_exhausted, request_count
            key = f"{provider.provider}:{category}"
            if provider.provider in rate_limited_providers:
                return
            try:
                if context is not None:
                    context.raise_if_cancelled()
                request_count += 1
                page = provider.discover(
                    category,
                    cursor=cursor,
                    page_size=page_size,
                    context=context,
                )
                if not isinstance(page, DiscoveryPage):
                    raise HistoricalProviderError("provider returned an invalid discovery page")
                if page.provider != provider.provider or page.category != category:
                    raise HistoricalProviderError("provider discovery provenance did not match its request")
                if (
                    type(page.request_count) is not int
                    or not 1 <= page.request_count <= REFRESH_REQUEST_MAX
                ):
                    raise HistoricalProviderError(
                        "provider discovery request count was invalid"
                    )
                request_count += page.request_count - 1
                for candidate in page.candidates:
                    if not isinstance(candidate, MagazineIssueCandidate):
                        raise HistoricalProviderError("provider returned an invalid magazine candidate")
                    if candidate.provider != provider.provider:
                        raise HistoricalProviderError("candidate provenance did not match its provider")
                discovered.extend(candidate for candidate in page.candidates if include_adult or not candidate.adult)
                if page.next_cursor is not None:
                    next_cursor = _opaque_cursor(page.next_cursor)
                    continuation_key = (provider.provider, category, next_cursor)
                    if continuation_key not in seen_continuations:
                        seen_continuations.add(continuation_key)
                        continuations.append((provider, category, next_cursor))
            except TaskDeadlineExceeded as error:
                errors[key] = f"{type(error).__name__}: {error}"[:1024]
                deadline_exhausted = True
            except TaskCancelled:
                raise
            except ProviderRateLimited as error:
                errors[key] = f"{type(error).__name__}: {error}"[:1024]
                rate_limited_providers.add(provider.provider)
            except Exception as error:
                errors[key] = f"{type(error).__name__}: {error}"[:1024]

        for provider, category in _initial_refresh_requests(
            self.providers,
            normalized_categories,
        ):
            if request_count >= REFRESH_REQUEST_MAX:
                break
            request_page(provider, category, None)
            if deadline_exhausted:
                break

        while (
            not deadline_exhausted
            and request_count < REFRESH_REQUEST_MAX - IA_METADATA_ENRICH_MAX_PER_PAGE
            and continuations
            and _catalog_needs_refill(existing, discovered, normalized_categories)
        ):
            provider, category, cursor = continuations.pop(0)
            request_page(provider, category, cursor)

        if context is not None:
            try:
                context.raise_if_cancelled()
            except TaskDeadlineExceeded as error:
                deadline_exhausted = True
                errors.setdefault(
                    "refresh:deadline",
                    f"{type(error).__name__}: {error}"[:1024],
                )
            except TaskCancelled:
                raise

        stored = self.store.merge(discovered) if discovered else existing
        added_count = sum(candidate.cover_id not in existing_ids for candidate in stored)
        return CatalogRefreshResult(
            candidates=stored,
            added_count=added_count,
            request_count=request_count,
            deadline_exhausted=deadline_exhausted,
            errors=MappingProxyType(errors),
        )


def _deduplicated_candidates(candidates):
    by_id = {}
    for candidate in candidates:
        if not isinstance(candidate, MagazineIssueCandidate):
            raise TypeError("catalog entries must be MagazineIssueCandidate values")
        by_id.pop(candidate.cover_id, None)
        by_id[candidate.cover_id] = candidate
    return tuple(by_id.values())


def _encoded_catalog(candidates):
    document = {
        "version": CATALOG_VERSION,
        "items": [candidate.to_document() for candidate in candidates],
    }
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


class MagazineHistoricalCatalogStore:
    """Atomically persist a newest-retained, byte- and item-bounded catalog."""

    def __init__(
        self,
        path,
        *,
        max_items=CATALOG_MAX_ITEMS,
        max_bytes=CATALOG_MAX_BYTES,
    ):
        if type(max_items) is not int or not 1 <= max_items <= CATALOG_MAX_ITEMS:
            raise ValueError(f"max_items must be between 1 and {CATALOG_MAX_ITEMS}")
        if type(max_bytes) is not int or not 256 <= max_bytes <= CATALOG_MAX_BYTES:
            raise ValueError(f"max_bytes must be between 256 and {CATALOG_MAX_BYTES}")
        self.path = Path(path)
        self.max_items = max_items
        self.max_bytes = max_bytes

    def load(self):
        try:
            if self.path.stat().st_size > self.max_bytes:
                return ()
            payload = self.path.read_bytes()
            document = json.loads(payload)
            if type(document) is not dict or frozenset(document) != {"version", "items"}:
                return ()
            if document.get("version") != CATALOG_VERSION:
                return ()
            items = document.get("items")
            if type(items) is not list or len(items) > self.max_items:
                return ()
            return _deduplicated_candidates(MagazineIssueCandidate.from_document(item) for item in items)
        except (FileNotFoundError, OSError, UnicodeError, ValueError, TypeError):
            return ()

    def save(self, candidates):
        normalized = _deduplicated_candidates(candidates)[-self.max_items :]
        payload = _encoded_catalog(normalized)
        if len(payload) > self.max_bytes:
            normalized, payload = self._largest_suffix_that_fits(normalized)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(self.path, payload, mode=0o600)
        return normalized

    def merge(self, candidates):
        return self.save((*self.load(), *tuple(candidates)))

    def _largest_suffix_that_fits(self, candidates):
        low = 0
        high = len(candidates)
        best_candidates = ()
        best_payload = _encoded_catalog(best_candidates)
        while low <= high:
            count = (low + high) // 2
            candidate_suffix = candidates[-count:] if count else ()
            payload = _encoded_catalog(candidate_suffix)
            if len(payload) <= self.max_bytes:
                best_candidates = candidate_suffix
                best_payload = payload
                low = count + 1
            else:
                high = count - 1
        return tuple(best_candidates), best_payload
