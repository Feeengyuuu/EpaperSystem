import feedparser
import hashlib
import html
import json
import logging
import os
import re
import tempfile

from utils.http_client import get_http_session

logger = logging.getLogger(__name__)
DEFAULT_COMIC_FEED_TIMEOUT_SECONDS = 10


COMICS = {
    "XKCD": {
        "feed": "https://xkcd.com/atom.xml",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: re.search(r'<img[^>]+src=["\"]([^"\"]+)["\"]', element).group(1),
        "title": lambda feed: feed.entries[0].title,
        "caption": lambda element: re.search(r'<img[^>]+alt=["\"]([^"\"]+)["\"]', element).group(1),
    },
    "Cyanide & Happiness": {
        "feed": "https://explosm-1311.appspot.com/",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: re.search(r'<img[^>]+src=["\"]([^"\"]+)["\"]', element).group(1),
        "title": lambda feed: feed.entries[0].title.split(" - ")[1].strip(),
        "caption": lambda element: "",
    },
    "Saturday Morning Breakfast Cereal": {
        "feed": "http://www.smbc-comics.com/comic/rss",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: re.search(r'<img[^>]+src=["\"]([^"\"]+)["\"]', element).group(1),
        "title": lambda feed: feed.entries[0].title.split("-")[1].strip(),
        "caption": lambda element: re.search(r'Hovertext:<br />(.*?)</p>', element).group(1),
    },
    "The Perry Bible Fellowship": {
        "feed": "https://pbfcomics.com/feed/",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: re.search(r'<img[^>]+src=["\"]([^"\"]+)["\"]', element).group(1),
        "title": lambda feed: feed.entries[0].title,
        "caption": lambda element: re.search(r'<img[^>]+alt=["\"]([^"\"]+)["\"]', element).group(1),
    },
    "Questionable Content": {
        "feed": "http://www.questionablecontent.net/QCRSS.xml",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: re.search(r'<img[^>]+src=["\"]([^"\"]+)["\"]', element).group(1),
        "title": lambda feed: feed.entries[0].title,
        "caption": lambda element: "",
    },
    "Poorly Drawn Lines": {
        "feed": "https://poorlydrawnlines.com/feed/",
        "element": lambda feed: feed.entries[0].get('content', [{}])[0].get('value', ''),
        "url": lambda element: re.search(r'<img[^>]+src=["\"]([^"\"]+)["\"]', element).group(1),
        "title": lambda feed: feed.entries[0].title,
        "caption": lambda element: "",
    },
    "Dinosaur Comics": {
        "feed": "https://www.qwantz.com/rssfeed.php",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: re.search(r'<img[^>]+src=["\"]([^"\"]+)["\"]', element).group(1),
        "title": lambda feed: feed.entries[0].title,
        "caption": lambda element: re.search(r'title="(.*?)" />', element.replace('\n', '')).group(1),
    },
    "webcomic name": {
        "feed": "https://webcomicname.com/rss",
        "element": lambda feed: feed.entries[0].description,
        "url": lambda element: re.search(r'<img[^>]+src=["\"]([^"\"]+)["\"]', element).group(1),
        "title": lambda feed: "",
        "caption": lambda element: "",
    },
}


def get_panel(comic_name):
    try:
        feed = _fetch_feed(COMICS[comic_name]["feed"])
        panel = _parse_panel(comic_name, feed)
        _write_panel_cache(comic_name, panel)
        return panel
    except Exception as exc:
        cached_panel = _read_panel_cache(comic_name)
        if cached_panel:
            logger.warning("Using cached comic panel for %s after feed failure: %s", comic_name, exc)
            return cached_panel
        logger.warning("Comic feed unavailable and no cache exists for %s: %s", comic_name, exc)
        return _placeholder_panel(comic_name)


def _fetch_feed(url):
    response = get_http_session().get(
        url,
        timeout=DEFAULT_COMIC_FEED_TIMEOUT_SECONDS,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False) and not getattr(feed, "entries", None):
        raise RuntimeError("Comic feed parser could not read the feed.")
    return feed


def _parse_panel(comic_name, feed):
    try:
        element = COMICS[comic_name]["element"](feed)
        return {
            "image_url": COMICS[comic_name]["url"](element),
            "title": html.unescape(COMICS[comic_name]["title"](feed)),
            "caption": html.unescape(COMICS[comic_name]["caption"](element)),
        }
    except Exception as exc:
        raise RuntimeError("Failed to retrieve latest comic.") from exc


def _panel_cache_path(comic_name):
    digest = hashlib.sha256(comic_name.encode("utf-8")).hexdigest()[:16]
    runtime_root = os.getenv("INKYPI_CACHE_DIR", "").strip()
    if runtime_root:
        cache_dir = os.path.join(os.path.expanduser(runtime_root), "comic")
    else:
        cache_dir = os.path.join(os.path.dirname(__file__), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"panel-{digest}.json")


def _write_panel_cache(comic_name, panel):
    cache_path = _panel_cache_path(comic_name)
    tmp_path = None

    def write_payload(target_path):
        with open(target_path, "w", encoding="utf-8") as outfile:
            json.dump(panel, outfile, ensure_ascii=False)
            outfile.write("\n")

    try:
        if os.name == "nt":
            write_payload(cache_path)
            return
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(cache_path)}.",
            suffix=".tmp",
            dir=os.path.dirname(cache_path),
        )
        os.close(fd)
        write_payload(tmp_path)
        try:
            os.replace(tmp_path, cache_path)
            tmp_path = None
        except OSError:
            logger.exception("Atomic comic panel cache replace failed; falling back to direct write: %s", cache_path)
            write_payload(cache_path)
    except Exception:
        logger.exception("Could not write comic panel cache: %s", cache_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning("Could not remove temporary comic panel cache file: %s", tmp_path)


def _read_panel_cache(comic_name):
    cache_path = _panel_cache_path(comic_name)
    try:
        with open(cache_path, "r", encoding="utf-8") as infile:
            panel = json.load(infile)
        if isinstance(panel, dict) and panel.get("image_url"):
            return panel
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception("Could not read comic panel cache: %s", cache_path)
    return None


def _placeholder_panel(comic_name):
    return {
        "image_url": None,
        "title": comic_name,
        "caption": "Comic feed temporarily unavailable. The next refresh will retry.",
        "placeholder": True,
    }
