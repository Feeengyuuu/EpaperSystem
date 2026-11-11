"""Identity-bound North America poster reuse and bounded media repair."""
from __future__ import annotations

from contextlib import closing
from datetime import timedelta
from html.parser import HTMLParser
import logging
import re
import unicodedata
from urllib.parse import unquote, urljoin, urlsplit

from plugins.box_office_top_movies.box_office_top_movies import (
    BoxOfficeMovie, IMAGE_HEADERS, REQUEST_HEADERS, TMDB_MOVIE_URL, TMDB_SEARCH_URL,
)
from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.safe_image import ImageLimits, safe_open_image_response

logger = logging.getLogger(__name__)
# The publisher's full-size posters reach 1350x2000 PNGs of about 3 MB (foreign releases).
POSTER_LIMITS = ImageLimits(max_bytes=4 * 1024 * 1024, max_width=1600,
                           max_height=2400, max_pixels=3_000_000)
# A foreign release's slug names its country after the year: Godzilla-Minus-Zero-(2026-Japan).
_SLUG_IDENTITY = re.compile(r"(.+)-\(((?:19|20)\d{2})(?:-[A-Za-z][A-Za-z-]*)?\)")
# Its heading and poster alt text add the original-language title in parentheses.
_ORIGINAL_TITLE = re.compile(r"\s*\([^()]*[^\x00-\x7f][^()]*\)")
# A film on the chart without a Simplified Chinese title is asked about again at TMDB, because
# TMDB's translators often add the title days after the film opens. The time of the next
# question is kept in the film's ``extra`` (saved with the cached chart and carried to the next
# chart): three hours after an answer without a title, so a new title reaches the page within
# hours while a film that never gets one costs at most eight small lookups a day; half an hour
# after a request that failed or that the poster lookup has just made.
LOCALIZED_TITLE_CHECK = "localized_title_check"
LOCALIZED_TITLE_RECHECK = timedelta(hours=3)
LOCALIZED_TITLE_RETRY = timedelta(minutes=30)
# At most this many films are asked about in one pass (two or three requests each); the others
# stay due and are taken by the next passes.
LOCALIZED_TITLES_PER_PASS = 2


def canonical_movie_url(value):
    """Accept a single official movie identity; fragments are presentation only."""
    try:
        parsed = urlsplit(str(value or ""))
        path = unquote(parsed.path).rstrip("/")
        if (parsed.scheme != "https" or parsed.hostname not in {"www.the-numbers.com", "the-numbers.com"}
                or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.query
                or not re.fullmatch(r"/movie/[A-Za-z0-9_.,'!()\-]+", path)
                or ".." in path):
            return None
        return "https://www.the-numbers.com" + path
    except (TypeError, ValueError):
        return None


def movie_identity(value):
    canonical = canonical_movie_url(value)
    if not canonical:
        return None
    slug = canonical.rsplit("/", 1)[-1]
    match = _SLUG_IDENTITY.fullmatch(slug)
    if not match:
        return None
    title = match[1].replace("-", " ")
    return canonical, title, match[2]


def title_key(value):
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    words = re.findall(r"[a-z0-9]+", value)
    # The Numbers puts a leading article at the end of some URL slugs.
    if words and words[-1] in {"a", "an", "the"}:
        words = [words[-1], *words[:-1]]
    return " ".join(words)


def matches_tmdb(movie, item):
    identity = movie_identity(movie.chart_url)
    if not identity or str(item.get("release_date") or "")[:4] != identity[2]:
        return False
    return title_key(identity[1]) in {
        title_key(item.get("title")), title_key(item.get("original_title")),
    }


def official_poster_url(value, canonical):
    """Only the actual same-film poster path on the publisher's media host."""
    try:
        parsed = urlsplit(str(value or ""))
        path = unquote(parsed.path)
        expected = canonical.rsplit("/", 1)[-1]
        return bool(parsed.scheme == "https" and parsed.hostname == "media.the-numbers.com"
                    and not parsed.username and not parsed.password and parsed.port in {None, 443}
                    and not parsed.query and not parsed.fragment
                    and re.fullmatch(re.escape("/images/movie-posters/" + expected)
                                     + r"\.(?:jpg|jpeg|png|webp)", path, re.I))
    except (TypeError, ValueError):
        return False


def valid_media_url(value, canonical):
    if official_poster_url(value, canonical):
        return True
    try:
        parsed = urlsplit(str(value or ""))
        return bool(parsed.scheme == "https" and parsed.hostname == "image.tmdb.org"
                    and not parsed.username and not parsed.password and parsed.port in {None, 443}
                    and not parsed.query and not parsed.fragment
                    and re.fullmatch(r"/t/p/(?:w\d+|original)/[A-Za-z0-9_-]+\.(?:jpg|png|webp)", parsed.path))
    except (TypeError, ValueError):
        return False


class _MoviePage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonicals = []
        self.og_urls = []
        self.headings = []
        self.images = []
        self._heading = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and "canonical" in attrs.get("rel", "").lower().split():
            self.canonicals.append(attrs.get("href", ""))
        if tag == "meta" and attrs.get("property", "").lower() == "og:url":
            self.og_urls.append(attrs.get("content", ""))
        if tag == "h1":
            self._heading = []
        if tag == "img":
            self.images.append((attrs.get("src", ""), attrs.get("alt", "")))

    def handle_data(self, data):
        if self._heading is not None:
            self._heading.append(data)

    def handle_endtag(self, tag):
        if tag == "h1" and self._heading is not None:
            self.headings.append(" ".join(" ".join(self._heading).split()))
            self._heading = None


def page_poster(html_text, requested_url, response_url):
    """Bind response identity, page title and image identity before returning a URL."""
    identity = movie_identity(requested_url)
    if not identity or canonical_movie_url(response_url) != identity[0]:
        return None
    parser = _MoviePage()
    parser.feed(html_text)
    if any(canonical_movie_url(urljoin(identity[0], url)) != identity[0] for url in parser.canonicals):
        return None
    for url in parser.og_urls:
        # Real movie pages carry the site's default homepage og:url. It is not
        # a movie identity; explicit canonical and movie-level OG remain strict.
        if url.rstrip("/") in {"https://www.the-numbers.com", "https://the-numbers.com"}:
            continue
        if canonical_movie_url(urljoin(identity[0], url)) != identity[0]:
            return None
    expected_heading = title_key(identity[1] + " " + identity[2])
    if not parser.headings or title_key(_ORIGINAL_TITLE.sub("", parser.headings[0])) != expected_heading:
        return None
    for source, alt in parser.images:
        source = urljoin(identity[0], source)
        # The publisher's alt text reads "<title> poster".
        alt_key = re.sub(r" poster$", "", title_key(_ORIGINAL_TITLE.sub("", alt)))
        if (official_poster_url(source, identity[0])
                and alt_key in {title_key(identity[1]), expected_heading}):
            return source
    return None


def reuse_posters(plugin, movies, previous):
    """Keep verified media only, never previous ranks, receipts or chart dates."""
    candidates = {}
    for row in previous:
        old = BoxOfficeMovie.from_dict(row)
        identity = movie_identity(old.chart_url)
        if identity:
            candidates.setdefault(identity[0], []).append(old)
    for movie in movies:
        identity = movie_identity(movie.chart_url)
        matches = candidates.get(identity[0], []) if identity else []
        if len(matches) != 1:
            continue
        old = matches[0]
        if ((old.release_year and old.release_year != identity[2])
                or not valid_media_url(old.poster_url, identity[0])
                or not plugin._restore_local_poster(old)):
            continue
        for name in ("poster_url", "poster_path", "tmdb_id", "release_year",
                     "localized_title", "localized_language"):
            setattr(movie, name, getattr(old, name))
        movie.extra = dict(movie.extra or {})
        for name in ("poster_source", "poster_market", "poster_language", LOCALIZED_TITLE_CHECK):
            if name in (old.extra or {}):
                movie.extra[name] = old.extra[name]


def _official_fallback(plugin, movie, budget):
    identity = movie_identity(movie.chart_url)
    if not identity:
        return
    with closing(budget.get(identity[0], headers=REQUEST_HEADERS, allow_redirects=False)) as response:
        response.raise_for_status()
        poster = page_poster(response.text, identity[0], response.url)
    if not poster or budget.remaining_seconds() <= 0:
        return
    _download_official_poster(plugin, movie, budget, identity, poster)


def _download_official_poster(plugin, movie, budget, identity, poster):
    # There is deliberately no guessed poster URL and no cross-host redirect.
    response = budget.get(poster, headers=IMAGE_HEADERS, stream=True, allow_redirects=False)
    with closing(response):
        if response.status_code != 200 or not official_poster_url(response.url, identity[0]):
            return
        with safe_open_image_response(response, limits=POSTER_LIMITS) as image:
            width, height = image.size
            if width < 120 or height < 180 or not 0.45 <= width / height <= 0.85:
                return
            path = plugin._poster_store().save(poster, image, protected_urls=plugin._protected_poster_urls())
    movie.poster_url, movie.poster_path = poster, str(path)
    movie.release_year = identity[2]
    movie.extra.update(poster_source="the_numbers", poster_market="US", poster_language="en")


def complete_posters(plugin, movies, settings, device_config, budget):
    """Fair missing-only repair under the caller's deadline; no chart requests."""
    plugin._poster_movies = movies
    missing = []
    for movie in movies:
        movie.extra = dict(movie.extra or {})
        identity = movie_identity(movie.chart_url)
        if not identity:
            continue
        if movie.poster_url and not valid_media_url(movie.poster_url, identity[0]):
            movie.poster_url = ""
        if not plugin._restore_local_poster(movie):
            missing.append(movie)

    def attempt(movie):
        value = movie.extra.get("poster_attempt", 0)
        return value if type(value) is int and value >= 0 else 0

    sequence = max((attempt(movie) for movie in movies), default=0)
    searched = []
    for movie in sorted(missing, key=lambda item: (attempt(item), item.rank)):
        if budget.remaining_seconds() <= 0:
            break
        sequence += 1
        movie.extra["poster_attempt"] = sequence
        try:
            identity = movie_identity(movie.chart_url)
            if official_poster_url(movie.poster_url, identity[0]):
                _download_official_poster(plugin, movie, budget, identity, movie.poster_url)
            elif movie.poster_url:
                plugin._download_posters([movie], session=budget)
            elif not movie.tmdb_id:
                # Prefer a correctly identified TMDb poster, then the chart publisher.
                searched.append(movie)
                plugin._enrich_with_tmdb([movie], settings, device_config, session=budget)
                plugin._download_posters([movie], session=budget)
            if not movie.poster_path and budget.remaining_seconds() > 0:
                _official_fallback(plugin, movie, budget)
        except TaskDeadlineExceeded:
            break
        except TaskCancelled:
            raise
        except Exception as error:
            logger.warning("North America poster repair deferred. | rank=%s error=%s", movie.rank, type(error).__name__)
    refresh_localized_titles(plugin, movies, settings, device_config, budget, just_searched=searched)


def _awaiting_localized_title(plugin, movies, settings, device_config):
    """The chart films that would show a Chinese title and have none; none without a TMDB key."""
    if (not plugin._truthy(settings.get("showLocalizedTitles"), True)
            or not plugin._tmdb_auth(settings, device_config)):
        return []
    return [movie for movie in movies
            if movie_identity(movie.chart_url)
            and not plugin._usable_localized_title(movie.localized_title, movie.title)]


def _title_check_time(movie):
    """The recorded time of the film's next title question as text, "" when there is none."""
    mark = (movie.extra or {}).get(LOCALIZED_TITLE_CHECK)
    return str(mark.get("retry_at") or "") if isinstance(mark, dict) else ""


def _next_title_check(plugin, movie, now):
    """When this film may be asked about again, or None when it is due now."""
    retry_at = plugin._parse_datetime(_title_check_time(movie))
    # A time further ahead than one interval (the clock moved back) is not trusted.
    return retry_at if retry_at and now < retry_at <= now + LOCALIZED_TITLE_RECHECK else None


def localized_title_followup(plugin, movies, settings, device_config):
    """The earliest time a film without a Chinese title is due to be asked about again (now for a
    film that is already due), or None when no film waits for one."""
    now = plugin._source_now()
    return min((_next_title_check(plugin, movie, now) or now
                for movie in _awaiting_localized_title(plugin, movies, settings, device_config)), default=None)


def _lookup_localized_title(plugin, movie, budget, auth, language):
    """TMDB's title for the film in ``language``, "" when it has none. A film TMDB has not
    identified yet (its poster came from the chart publisher) is first searched for and matched
    by the same year-and-title rule as the poster lookup."""
    if not movie.tmdb_id:
        results = plugin._tmdb_get_json(budget, TMDB_SEARCH_URL, auth, {
            "query": movie.title, "include_adult": "false", "language": "en-US", "region": "US",
        }).get("results") or []
        item = plugin._select_tmdb_search_result(movie, results)
        if not item or not item.get("id"):
            return ""
        movie.tmdb_id = item["id"]
    detail = plugin._tmdb_get_json(budget, TMDB_MOVIE_URL.format(movie_id=movie.tmdb_id), auth,
                                   {"language": language})
    return (plugin._usable_localized_title(detail.get("title"), movie.title)
            or plugin._localized_title_from_alternatives(movie, budget, auth, language))


def refresh_localized_titles(plugin, movies, settings, device_config, budget, *, just_searched=()):
    """Ask TMDB again for the Chinese title of films that still have none, after the posters and
    within what is left of the caller's budget. Each film is asked about at most once per
    interval and at most ``LOCALIZED_TITLES_PER_PASS`` films per pass, the longest-waiting first;
    a film with a title costs nothing. ``just_searched`` are the films the poster lookup asked
    TMDB about in this pass: their title was part of that lookup, so they wait for a later pass.
    Optional: a failed request leaves the film as it was, to be asked about again later."""
    waiting = _awaiting_localized_title(plugin, movies, settings, device_config)
    if not waiting:
        return
    auth = plugin._tmdb_auth(settings, device_config)
    language = str(settings.get("localizedLanguage") or "zh-CN").strip() or "zh-CN"
    now = plugin._source_now()

    def wait(movie, interval):
        movie.extra = {**(movie.extra or {}), LOCALIZED_TITLE_CHECK: {"retry_at": (now + interval).isoformat()}}

    due = []
    for movie in waiting:
        if any(movie is other for other in just_searched):
            if movie.tmdb_id:
                wait(movie, LOCALIZED_TITLE_RETRY)
        elif _next_title_check(plugin, movie, now) is None:
            due.append(movie)
    # A film never asked about comes first, then the one whose last question is oldest.
    due.sort(key=lambda movie: (_title_check_time(movie), movie.rank))
    for movie in due[:LOCALIZED_TITLES_PER_PASS]:
        if budget.remaining_seconds() <= 0:
            break
        # Recorded before asking: whatever the request does, the film is not asked about again
        # until the short retry interval has passed.
        wait(movie, LOCALIZED_TITLE_RETRY)
        try:
            title = _lookup_localized_title(plugin, movie, budget, auth, language)
        except TaskDeadlineExceeded:
            break
        except TaskCancelled:
            raise
        except Exception as error:
            logger.warning("North America Chinese title lookup deferred. | rank=%s error=%s",
                           movie.rank, type(error).__name__)
            continue
        if title:
            movie.localized_title, movie.localized_language = title, language
            movie.extra.pop(LOCALIZED_TITLE_CHECK, None)
            logger.info("North America Chinese title found. | rank=%s", movie.rank)
        else:
            wait(movie, LOCALIZED_TITLE_RECHECK)
