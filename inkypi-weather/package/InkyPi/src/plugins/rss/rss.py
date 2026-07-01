from plugins.base_plugin.base_plugin import BasePlugin
from utils.http_client import get_http_session
import feedparser
import hashlib
import html
import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

FONT_SIZES = {
    "x-small": 0.7,
    "small": 0.9,
    "normal": 1,
    "large": 1.1,
    "x-large": 1.3
}

class Rss(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = True
        return template_params

    def generate_image(self, settings, device_config):
        title = settings.get("title")
        feed_url = settings.get("feedUrl")
        if not feed_url:
            raise RuntimeError("RSS Feed Url is required.")
        
        items = self.parse_rss_feed(feed_url)

        dimensions = self.get_dimensions(device_config)

        template_params = {
            "title": title,
            "include_images": settings.get("includeImages") == "true",
            "items": items[:10],
            "font_scale": FONT_SIZES.get(settings.get('fontSize', 'normal'), 1),
            "plugin_settings": settings
        }

        image = self.render_image(dimensions, "rss.html", "rss.css", template_params)
        return image
    
    def parse_rss_feed(self, url, timeout=10):
        try:
            session = get_http_session()
            resp = session.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()

            feed = feedparser.parse(resp.content)
            if getattr(feed, "bozo", False) and not getattr(feed, "entries", None):
                raise RuntimeError("RSS parser could not read the feed.")

            items = self._feed_items(feed)
            if not items:
                raise RuntimeError("RSS feed contains no entries.")

            self._write_feed_cache(url, items)
            return items
        except Exception as exc:
            cached_items = self._read_feed_cache(url)
            if cached_items:
                logger.warning("Using cached RSS feed after fetch/parse failure for %s: %s", url, exc)
                return cached_items
            logger.warning("RSS feed unavailable and no cache exists for %s: %s", url, exc)
            return [self._placeholder_item(url)]

    def _feed_items(self, feed):
        items = []
        for entry in feed.entries:
            item = {
                "title": html.unescape(entry.get("title", "")),
                "description": html.unescape(entry.get("description", "")),
                "published": entry.get("published", ""),
                "link": entry.get("link", ""),
                "image": None,
            }

            if "media_content" in entry and len(entry.media_content) > 0:
                item["image"] = entry.media_content[0].get("url")
            elif "media_thumbnail" in entry and len(entry.media_thumbnail) > 0:
                item["image"] = entry.media_thumbnail[0].get("url")
            elif "enclosures" in entry and len(entry.enclosures) > 0:
                item["image"] = entry.enclosures[0].get("url")

            items.append(item)
        return items

    def _rss_cache_path(self, url):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir(leaf="cache") / f"rss-{digest}.json"

    def _write_feed_cache(self, url, items):
        cache_path = self._rss_cache_path(url)
        tmp_path = None
        payload = {"url": url, "items": items}

        def write_payload(target_path):
            with open(target_path, "w", encoding="utf-8") as outfile:
                json.dump(payload, outfile, ensure_ascii=False)
                outfile.write("\n")

        try:
            if os.name == "nt":
                write_payload(cache_path)
                return
            fd, tmp_path = tempfile.mkstemp(
                prefix=f".{cache_path.stem}.",
                suffix=".tmp",
                dir=str(cache_path.parent),
            )
            os.close(fd)
            write_payload(tmp_path)
            try:
                os.replace(tmp_path, cache_path)
                tmp_path = None
            except OSError:
                logger.exception("Atomic RSS cache replace failed; falling back to direct write: %s", cache_path)
                write_payload(cache_path)
        except Exception:
            logger.exception("Could not write RSS cache: %s", cache_path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.warning("Could not remove temporary RSS cache file: %s", tmp_path)

    def _read_feed_cache(self, url):
        cache_path = self._rss_cache_path(url)
        try:
            with open(cache_path, "r", encoding="utf-8") as infile:
                payload = json.load(infile)
            items = payload.get("items")
            if isinstance(items, list):
                return items
        except FileNotFoundError:
            return None
        except Exception:
            logger.exception("Could not read RSS cache: %s", cache_path)
        return None

    def _placeholder_item(self, url):
        return {
            "title": "RSS feed temporarily unavailable",
            "description": f"Could not fetch or parse {url}. The next refresh will retry.",
            "published": "",
            "link": url,
            "image": None,
        }