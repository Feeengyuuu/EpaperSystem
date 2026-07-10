from plugins.base_plugin.base_plugin import BasePlugin
from plugins.plugin_settings import resolve_refresh_on_display
from datetime import datetime, timedelta
import html
from io import BytesIO
from pathlib import Path
import sys
from urllib.parse import urlparse
from utils.app_utils import get_font
from utils.image_utils import get_image, take_screenshot, text_width
from PIL import Image, ImageDraw, ImageFont
import hashlib
import json
import logging
import os
import re
import requests
from plugins.newspaper.constants import NEWSPAPERS

logger = logging.getLogger(__name__)

FREEDOM_FORUM_URL = "https://cdn.freedomforum.org/dfp/jpg{}/lg/{}.jpg"
LYWB_A01_PDF_URL = "https://lywb.lyd.com.cn/images2/2/{year_month}/{day}/A01/{stamp}A01_pdf.pdf"
LYWB_LOOKBACK_DAYS = 10
NEWS_FRONTPAGE_ROTATION_VERSION = "news-frontpage-rotation-v1"
DEFAULT_MEDIA_SOURCES = """BBC News|url|https://www.bbc.com/news
CNN|url|https://www.cnn.com
CCTV News|url|https://news.cctv.com/index.shtml
Xinhua|url|https://www.xinhuanet.com/
Luoyang Evening News|lywb|A01
China Daily|newspaper|chi_cd
People's Daily|newspaper|chi_pd
The New York Times|newspaper|ny_nyt
The Washington Post|newspaper|dc_wp
USA Today|newspaper|usat"""

TRADITIONAL_TO_SIMPLIFIED = str.maketrans({
    "\u4e26": "\u5e76",
    "\u4e9e": "\u4e9a",
    "\u4f48": "\u5e03",
    "\u50f9": "\u4ef7",
    "\u5104": "\u4ebf",
    "\u5167": "\u5185",
    "\u5169": "\u4e24",
    "\u52d5": "\u52a8",
    "\u52d9": "\u52a1",
    "\u570b": "\u56fd",
    "\u5831": "\u62a5",
    "\u5834": "\u573a",
    "\u5c0e": "\u5bfc",
    "\u5c08": "\u4e13",
    "\u5c0d": "\u5bf9",
    "\u5c64": "\u5c42",
    "\u5ee3": "\u5e7f",
    "\u5f8c": "\u540e",
    "\u6771": "\u4e1c",
    "\u689d": "\u6761",
    "\u696d": "\u4e1a",
    "\u6a19": "\u6807",
    "\u6a5f": "\u673a",
    "\u6aa2": "\u68c0",
    "\u6b50": "\u6b27",
    "\u6b0a": "\u6743",
    "\u6c23": "\u6c14",
    "\u6fdf": "\u6d4e",
    "\u70ba": "\u4e3a",
    "\u7522": "\u4ea7",
    "\u756b": "\u753b",
    "\u767c": "\u53d1",
    "\u7bc0": "\u8282",
    "\u7d00": "\u7eaa",
    "\u7d1a": "\u7ea7",
    "\u7d50": "\u7ed3",
    "\u7d71": "\u7edf",
    "\u7d93": "\u7ecf",
    "\u7dda": "\u7ebf",
    "\u7e3d": "\u603b",
    "\u7db2": "\u7f51",
    "\u8077": "\u804c",
    "\u805e": "\u95fb",
    "\u8207": "\u4e0e",
    "\u842c": "\u4e07",
    "\u83ef": "\u534e",
    "\u862d": "\u5170",
    "\u969b": "\u9645",
    "\u8655": "\u5904",
    "\u89c0": "\u89c2",
    "\u8a08": "\u8ba1",
    "\u8a0a": "\u8baf",
    "\u8a2d": "\u8bbe",
    "\u8a55": "\u8bc4",
    "\u8a71": "\u8bdd",
    "\u8a9e": "\u8bed",
    "\u8abf": "\u8c03",
    "\u8ad6": "\u8bba",
    "\u8b70": "\u8bae",
    "\u8b8a": "\u53d8",
    "\u8ca1": "\u8d22",
    "\u8cbf": "\u8d38",
    "\u8cc7": "\u8d44",
    "\u8cfd": "\u8d5b",
    "\u8eca": "\u8f66",
    "\u8f49": "\u8f6c",
    "\u8f09": "\u8f7d",
    "\u9078": "\u9009",
    "\u91ab": "\u533b",
    "\u91cb": "\u91ca",
    "\u91dd": "\u9488",
    "\u9577": "\u957f",
    "\u9580": "\u95e8",
    "\u958b": "\u5f00",
    "\u9593": "\u95f4",
    "\u95dc": "\u5173",
    "\u96fb": "\u7535",
    "\u9801": "\u9875",
    "\u9818": "\u9886",
    "\u982d": "\u5934",
    "\u983b": "\u9891",
    "\u984c": "\u9898",
    "\u98a8": "\u98ce",
    "\u9ad4": "\u4f53",
    "\u9ede": "\u70b9",
})

MOJIBAKE_MARKERS = (
    "\ufffd",
    "\u00c3",
    "\u00c2",
    "\u00e5",
    "\u00e6",
    "\u00e7",
    "\u00e8",
    "\u00e9",
    "\u5d15",
    "\u5a34",
    "\u626e",
    "\u701b",
    "\u752f",
    "\u93c2",
)


def _enabled(value, default=False):
    if value is None:
        return default
    return value is True or str(value).lower() in {"1", "true", "on", "yes", "rotate"}


class Newspaper(BasePlugin):
    def wants_refresh_on_display(self, settings):
        settings = settings or {}
        rotation_default = (
            str(settings.get("mediaRotationMode") or "rotate").lower()
            != "single"
        )
        return resolve_refresh_on_display(
            settings,
            self.config,
            base_default=rotation_default,
        )

    def generate_image(self, settings, device_config):
        if self._rotation_enabled(settings):
            sources = self._parse_media_sources(settings.get("mediaSources") or DEFAULT_MEDIA_SOURCES)
            if sources:
                return self._generate_rotating_image(sources, device_config)

        newspaper_slug = settings.get("newspaperSlug")
        if not newspaper_slug:
            raise RuntimeError("Newspaper input not provided.")

        image = self._fetch_newspaper_cover(newspaper_slug, device_config)
        if not image:
            raise RuntimeError("Newspaper front cover not found.")

        return image

    def _rotation_enabled(self, settings):
        mode = settings.get("mediaRotationMode")
        if mode:
            return str(mode).lower() != "single"
        return _enabled(settings.get("mediaRotationEnabled"), default=True)

    def _parse_media_sources(self, sources_text):
        sources = []
        seen = set()
        for line in (sources_text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [part.strip() for part in line.split("|")]
            name = ""
            source_type = ""
            value = ""

            if len(parts) >= 3:
                name, source_type, value = parts[0], parts[1].lower(), parts[2]
            elif len(parts) == 2:
                name, value = parts
                source_type = "url" if value.startswith(("http://", "https://")) else "newspaper"
            else:
                value = parts[0]
                source_type = "url" if value.startswith(("http://", "https://")) else "newspaper"

            if source_type in {"web", "website", "screenshot"}:
                source_type = "url"
            if source_type in {"headline", "headlines", "text"}:
                source_type = "headlines"
            if source_type in {"luoyang", "luoyang_evening_news", "lywb"}:
                source_type = "lywb"
            if source_type in {"paper", "slug", "frontpage"}:
                source_type = "newspaper"
            if source_type not in {"url", "headlines", "lywb", "newspaper"} or not value:
                logger.warning("Ignoring invalid media source line: %s", line)
                continue

            if source_type in {"url", "headlines"}:
                if not value.startswith(("http://", "https://")):
                    logger.warning("Ignoring media source with invalid URL: %s", line)
                    continue
                default_name = urlparse(value).netloc or value
                identity_value = value
            elif source_type == "lywb":
                value = value.upper()
                default_name = "Luoyang Evening News"
                identity_value = value
            else:
                value = value.upper()
                default_name = value
                identity_value = value

            source_id = f"{source_type}:{identity_value}"
            if source_id in seen:
                continue
            seen.add(source_id)
            sources.append({
                "id": source_id,
                "name": name or default_name,
                "type": source_type,
                "value": value,
            })

        return sources

    def _generate_rotating_image(self, sources, device_config):
        errors = []
        for _ in range(len(sources)):
            source = self._select_next_source(sources)
            try:
                image = self._fetch_source_image(source, device_config)
            except Exception as exc:
                logger.warning("News front page failed for %s: %s", source["name"], exc)
                errors.append(f"{source['name']}: {exc}")
                continue

            if image:
                logger.info("Selected news front page: %s", source["name"])
                return image

            errors.append(f"{source['name']}: no image")

        detail = "; ".join(errors[-4:])
        raise RuntimeError(f"No news front page could be fetched. {detail}")

    def _fetch_source_image(self, source, device_config):
        if source["type"] == "headlines":
            headlines = self._fetch_web_headlines(source["value"])
            if headlines:
                return self._render_headlines_page(source, headlines, device_config)
            return None

        if source["type"] == "url":
            image = self._fetch_url_screenshot(source["value"], device_config)
            if image:
                return image
            return None

        if source["type"] == "lywb":
            return self._fetch_luoyang_evening_news_cover(device_config)

        return self._fetch_newspaper_cover(source["value"], device_config)

    def _fetch_url_screenshot(self, url, device_config):
        dimensions = self.get_dimensions(device_config)

        logger.info("Taking news front page screenshot: %s", url)
        return take_screenshot(url, dimensions, timeout_ms=40000)

    def _fetch_web_headlines(self, url):
        try:
            response = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": "InkyPi News Front Pages/1.0"},
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Could not fetch news front page HTML %s: %s", url, exc)
            return []

        return self._extract_headlines(self._decode_response_text(response))

    def _decode_response_text(self, response):
        encodings = []

        content_type = response.headers.get("content-type", "")
        for match in re.finditer(r"charset\s*=\s*['\"]?([A-Za-z0-9._-]+)", content_type, re.I):
            encodings.append(match.group(1))

        head = response.content[:4096].decode("ascii", errors="ignore")
        for match in re.finditer(r"charset\s*=\s*['\"]?([A-Za-z0-9._-]+)", head, re.I):
            encodings.append(match.group(1))

        encodings.extend([
            response.encoding,
            getattr(response, "apparent_encoding", None),
            "utf-8",
            "gb18030",
            "gbk",
            "big5",
        ])

        best_text = None
        best_score = None
        seen = set()
        for encoding in encodings:
            if not encoding:
                continue
            encoding_key = encoding.lower()
            if encoding_key in seen:
                continue
            seen.add(encoding_key)

            try:
                text = response.content.decode(encoding, errors="replace")
            except LookupError:
                continue

            score = (text.count("\ufffd") * 10) + self._mojibake_score(text)
            if best_score is None or score < best_score:
                best_text = text
                best_score = score

        if best_text is None:
            best_text = response.text

        return self._repair_chinese_mojibake(best_text)

    def _extract_headlines(self, html_text):
        html_text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html_text or "")
        candidates = []
        preferred = []

        for match in re.finditer(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", html_text):
            text = self._clean_html_text(match.group(1))
            if self._looks_like_headline(text):
                candidates.append(text)

        for match in re.finditer(r"(?is)<a\b([^>]*)>(.*?)</a>", html_text):
            attrs = match.group(1) or ""
            text = self._clean_html_text(match.group(2))
            if not self._looks_like_headline(text):
                continue
            if "ckxxapp.ckxx.net" in attrs:
                preferred.append(text)
            else:
                candidates.append(text)

        unique = []
        seen = set()
        for text in preferred + candidates:
            key = re.sub(r"\W+", "", text.lower())
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(text)
            if len(unique) >= 9:
                break
        return unique

    def _clean_html_text(self, value):
        value = re.sub(r"<[^>]+>", " ", value or "")
        value = html.unescape(value)
        value = re.sub(r"\s+", " ", value).strip()
        value = self._repair_chinese_mojibake(value)
        return self._to_simplified_chinese(value)

    def _looks_like_headline(self, text):
        if not text or len(text) > 140:
            return False
        lower = text.lower()
        reject = {
            "sign in",
            "subscribe",
            "privacy policy",
            "terms of use",
            "cookie",
            "advertisement",
            "direct sponsorship",
            "edition",
            "weather",
            "video",
            "live tv",
            "首页",
            "平台热榜",
            "主题聚合",
            "历史归档",
            "广告投放",
            "联系投放",
            "其他平台",
            "参考消息实时热搜榜",
            "在 hotflashnews 投放广告",
        }
        reject_contains = [
            "投放广告",
            "aads",
            "direct sponsorship",
        ]
        if (
            lower in reject
            or any(lower.startswith(prefix) for prefix in ["skip to", "follow ", "share "])
            or any(marker in lower for marker in reject_contains)
        ):
            return False
        cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        if cjk_count >= 4:
            return True
        if len(text) < 12:
            return False
        return len(re.findall(r"[A-Za-z]{3,}", text)) >= 4

    def _repair_chinese_mojibake(self, text):
        if not text or self._mojibake_score(text) < 2:
            return text

        candidates = [text]
        for encoding in ["gb18030", "gbk", "latin1", "cp1252"]:
            try:
                candidates.append(text.encode(encoding, errors="strict").decode("utf-8"))
            except Exception:
                try:
                    candidates.append(text.encode(encoding, errors="ignore").decode("utf-8", errors="ignore"))
                except Exception:
                    continue

        return min(candidates, key=self._text_quality_score)

    def _mojibake_score(self, text):
        return sum(text.count(marker) for marker in MOJIBAKE_MARKERS)

    def _text_quality_score(self, text):
        cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        return (self._mojibake_score(text) * 20) + (text.count("?") * 2) - cjk_count

    def _to_simplified_chinese(self, text):
        if not self._has_cjk(text):
            return text

        converter = self._get_simplified_converter()
        if converter:
            try:
                return converter(text)
            except Exception as exc:
                logger.warning("Could not convert headline text to simplified Chinese: %s", exc)

        return text.translate(TRADITIONAL_TO_SIMPLIFIED)

    def _get_simplified_converter(self):
        if hasattr(self, "_simplified_converter"):
            return self._simplified_converter

        converter = None
        try:
            from opencc import OpenCC

            opencc_converter = OpenCC("t2s")
            converter = opencc_converter.convert
        except Exception:
            try:
                from zhconv import convert

                converter = lambda value: convert(value, "zh-cn")
            except Exception:
                converter = None

        self._simplified_converter = converter
        return converter

    def _render_headlines_page(self, source, headlines, device_config):
        dimensions = self.get_dimensions(device_config)
        width, height = dimensions
        headlines = [self._to_simplified_chinese(self._repair_chinese_mojibake(headline)) for headline in headlines]
        has_chinese = self._has_cjk(source["name"] + " " + " ".join(headlines))

        image = Image.new("RGB", dimensions, (255, 255, 255))
        draw = ImageDraw.Draw(image)
        black = (0, 0, 0)
        white = (255, 255, 255)

        title_font = self._font(32, bold=True)
        meta_font = self._font(14)
        item_font = self._font(20)
        source_font = self._font(13)

        draw.rectangle((0, 0, width, 64), fill=black)
        draw.text((18, 12), source["name"][:34], fill=white, font=title_font)
        subtitle = "\u6587\u5b57\u5934\u7248" if has_chinese else "front page headlines fallback"
        draw.text((18, 46), subtitle, fill=white, font=meta_font)

        host = urlparse(source["value"]).netloc or source["value"]
        draw.text((width - 18, 46), host[:34], fill=white, font=source_font, anchor="ra")

        y = 86
        line_gap = 6
        for index, headline in enumerate(headlines[:8], start=1):
            prefix = f"{index}."
            draw.text((20, y), prefix, fill=black, font=item_font)
            lines = self._wrap_text(draw, headline, item_font, width - 78)
            x = 58
            for line in lines[:2]:
                draw.text((x, y), line, fill=black, font=item_font)
                y += self._text_height(draw, line, item_font) + 2
            y += line_gap
            if y > height - 36:
                break

        generated = datetime.now().strftime("%Y-%m-%d %H:%M")
        generated_label = "\u751f\u6210" if has_chinese else "Generated"
        draw.line((18, height - 28, width - 18, height - 28), fill=black, width=1)
        draw.text((18, height - 22), f"{generated_label} {generated}", fill=black, font=source_font)
        return image

    def _font(self, size, bold=False):
        for family in ["LXGW WenKai", "FandolKai", "I.Ming", "Jost"]:
            try:
                font = get_font(family, size, "bold" if bold else "normal")
            except Exception as exc:
                logger.warning("Could not load font %s: %s", family, exc)
                font = None
            if font:
                return font

        src_dir = Path(__file__).resolve().parents[2]
        for relative_path in [
            Path("static") / "fonts" / "LXGWWenKai-Regular.ttf",
            Path("plugins") / "chinese_literature_clock" / "fonts" / "FandolKai-Regular.otf",
            Path("plugins") / "chinese_literature_clock" / "fonts" / "I.Ming-8.10.ttf",
        ]:
            try:
                font_path = src_dir / relative_path
                if font_path.is_file():
                    return ImageFont.truetype(str(font_path), size)
            except Exception as exc:
                logger.warning("Could not load font file %s: %s", relative_path, exc)

        return ImageFont.load_default()

    def _wrap_text(self, draw, text, font, max_width):
        tokens = list(text) if self._has_cjk(text) else text.split()
        separator = "" if self._has_cjk(text) else " "
        lines = []
        current = ""
        for token in tokens:
            candidate = token if not current else f"{current}{separator}{token}"
            if self._text_width(draw, candidate, font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = token
        if current:
            lines.append(current)
        return lines or [text]

    def _has_cjk(self, text):
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    def _text_width(self, draw, text, font):
        return text_width(draw, text, font)

    def _text_height(self, draw, text, font):
        box = draw.textbbox((0, 0), text or "A", font=font)
        return box[3] - box[1]

    def _fetch_newspaper_cover(self, newspaper_slug, device_config):
        newspaper_slug = newspaper_slug.upper()

        # Get today's date
        today = datetime.today()

        # check the next day, then today, then prior day
        days = [today + timedelta(days=diff) for diff in [1, 0, -1, -2]]

        image = None
        for date in days:
            image_url = FREEDOM_FORUM_URL.format(date.day, newspaper_slug)
            image = get_image(image_url)
            if image:
                logger.info(f"Found {newspaper_slug} front cover for {date.strftime('%Y-%m-%d')}")
                break

        if image:
            # expand height if newspaper is wider than resolution
            img_width, img_height = image.size

            dimensions = device_config.get_resolution()
            if device_config.get_config("orientation") == "horizontal":
                dimensions = dimensions[::-1]

            desired_width, desired_height = dimensions

            img_ratio = img_width / img_height
            desired_ratio = desired_width / desired_height

            if img_ratio < desired_ratio:
                new_height = int((img_width * desired_width) / desired_height)
                new_image = Image.new("RGB", (img_width, new_height), (255, 255, 255))
                new_image.paste(image, (0, 0))
                image = new_image
        else:
            return None

        return image

    def _fetch_luoyang_evening_news_cover(self, device_config):
        for date in self._lywb_candidate_dates():
            url = self._build_lywb_pdf_url(date)
            pdf_bytes = self._download_pdf(url)
            if not pdf_bytes:
                continue

            image = self._render_pdf_first_page(pdf_bytes)
            if not image:
                continue

            logger.info("Found Luoyang Evening News front page for %s", date.strftime("%Y-%m-%d"))
            return self._prepare_frontpage_image(image, device_config)

        return None

    def _lywb_candidate_dates(self):
        # Luoyang is UTC+8; use source-local date instead of the device timezone.
        today = datetime.utcnow() + timedelta(hours=8)
        return [today - timedelta(days=diff) for diff in range(LYWB_LOOKBACK_DAYS + 1)]

    def _build_lywb_pdf_url(self, date):
        return LYWB_A01_PDF_URL.format(
            year_month=date.strftime("%Y-%m"),
            day=date.strftime("%d"),
            stamp=date.strftime("%Y%m%d"),
        )

    def _download_pdf(self, url):
        try:
            response = requests.get(
                url,
                timeout=20,
                headers={
                    "User-Agent": "Mozilla/5.0 InkyPi News Front Pages/1.0",
                    "Referer": "https://lywb.lyd.com.cn/",
                },
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
        except Exception as exc:
            logger.warning("Could not fetch PDF front page %s: %s", url, exc)
            return None

        if not response.content.startswith(b"%PDF"):
            logger.warning("PDF front page response was not a PDF: %s", url)
            return None

        return response.content

    def _render_pdf_first_page(self, pdf_bytes):
        try:
            fitz = self._import_pymupdf()
        except Exception as exc:
            try:
                image = Image.open(BytesIO(pdf_bytes))
                image.load()
                return image.convert("RGB")
            except Exception:
                raise RuntimeError("PyMuPDF is required to render PDF front pages") from exc

        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            if len(document) < 1:
                return None

            page = document.load_page(0)
            matrix = fitz.Matrix(2, 2)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        finally:
            document.close()

    def _import_pymupdf(self):
        try:
            import fitz

            return fitz
        except Exception:
            vendor_path = Path(__file__).resolve().parent / "_vendor"
            if vendor_path.is_dir() and str(vendor_path) not in sys.path:
                sys.path.insert(0, str(vendor_path))

            import fitz

            return fitz

    def _prepare_frontpage_image(self, image, device_config):
        img_width, img_height = image.size

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "horizontal":
            dimensions = dimensions[::-1]

        desired_width, desired_height = dimensions

        img_ratio = img_width / img_height
        desired_ratio = desired_width / desired_height

        if img_ratio < desired_ratio:
            new_height = int((img_width * desired_width) / desired_height)
            new_image = Image.new("RGB", (img_width, new_height), (255, 255, 255))
            new_image.paste(image, (0, 0))
            image = new_image

        return image

    def _select_next_source(self, sources):
        pool_key = self._source_pool_key(sources)
        state = self._read_rotation_state()
        pool_state = state.get(pool_key, {})
        next_index = int(pool_state.get("next_index") or 0) % len(sources)
        selected = sources[next_index]

        state[pool_key] = {
            "next_index": (next_index + 1) % len(sources),
            "last_selected": selected["id"],
            "pool_size": len(sources),
            "source_ids": [source["id"] for source in sources],
        }
        self._write_rotation_state(state)
        return selected

    def _source_pool_key(self, sources):
        raw = "|".join(
            [NEWS_FRONTPAGE_ROTATION_VERSION]
            + [source["id"] for source in sources]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _rotation_state_path(self):
        return self.cache_dir() / ".newspaper_rotation_state.json"

    def _read_rotation_state(self):
        path = self._rotation_state_path()
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read newspaper rotation state %s: %s", path, exc)
        return {}

    def _write_rotation_state(self, state):
        path = self._rotation_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(state, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            path.write_text(text, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['newspapers'] = sorted(NEWSPAPERS, key=lambda n: n['name'])
        template_params["default_media_sources"] = DEFAULT_MEDIA_SOURCES
        return template_params
