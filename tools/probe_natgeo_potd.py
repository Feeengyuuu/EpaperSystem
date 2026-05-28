from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "inkypi-weather" / "package" / "InkyPi" / "src"
LOCAL_PACKAGES = ROOT / "inkypi-weather" / "package" / "InkyPi" / ".pc-packages"

if LOCAL_PACKAGES.exists():
    sys.path.insert(0, str(LOCAL_PACKAGES))
sys.path.insert(0, str(SRC))

from PIL import Image, ImageEnhance, ImageFilter, ImageOps  # noqa: E402


PAGE_URL = "https://www.nationalgeographic.com/photo-of-the-day"
OFFICIAL_LOGO_URL = "https://assets-cdn.nationalgeographic.com/natgeo/static/icons/redesign-logo.svg"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; InkyPi NatGeoPOTDProbe/1.0; "
        "+https://github.com/fatihak/InkyPi/)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
IMAGE_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5",
    "Referer": PAGE_URL,
}


class CandidateParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.meta = []
        self.images = []
        self.links = []
        self._in_title = False
        self._title_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
            self._title_text = []
            return

        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
                self._add(self.meta, attrs.get("content"), key)
            return

        if tag == "img":
            for url in self._urls_from_image_attrs(attrs):
                self._add(
                    self.images,
                    url,
                    " ".join([attrs.get("alt") or "", attrs.get("class") or "", attrs.get("id") or ""]),
                )
            return

        if tag == "source":
            for attr in ["srcset", "data-srcset"]:
                if attrs.get(attr):
                    self._add(self.images, self._best_srcset_url(attrs[attr]), attrs.get("media") or attr)
            return

        if tag == "a" and attrs.get("href"):
            self._add(self.links, attrs["href"], attrs.get("aria-label") or attrs.get("title") or "")

    def handle_data(self, data):
        if self._in_title:
            self._title_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "title" and self._in_title:
            self.title = re.sub(r"\s+", " ", " ".join(self._title_text)).strip()
            self._in_title = False

    def _add(self, bucket, url, label):
        if not url:
            return
        bucket.append({"url": urljoin(self.base_url, html.unescape(url)), "label": label or ""})

    def _urls_from_image_attrs(self, attrs):
        urls = []
        for attr in ["src", "data-src", "data-original"]:
            if attrs.get(attr):
                urls.append(attrs[attr])
        for attr in ["srcset", "data-srcset"]:
            if attrs.get(attr):
                urls.append(self._best_srcset_url(attrs[attr]))
        return [url for url in urls if url]

    def _best_srcset_url(self, srcset):
        best_url = ""
        best_score = -1
        for part in srcset.split(","):
            bits = part.strip().split()
            if not bits:
                continue
            score = 0
            if len(bits) > 1:
                match = re.search(r"(\d+)(?:w|x)?$", bits[-1])
                if match:
                    score = int(match.group(1))
            if score >= best_score:
                best_score = score
                best_url = bits[0]
        return best_url


def main() -> int:
    runs = 3
    out_dir = ROOT / ".tmp" / "natgeo_potd_probe"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for index in range(runs):
        result = probe_once(out_dir, index)
        results.append(result)
        print(
            f"run={index + 1} status={result['status']} "
            f"title={result.get('page_title')!r} image={result.get('image_url')}"
        )
        if index < runs - 1:
            time.sleep(1)

    meta_path = out_dir / "probe_results.json"
    meta_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    successes = [result for result in results if result["status"] == "ok"]
    if not successes:
        raise RuntimeError("No NatGeo Photo of the Day image could be downloaded.")

    unique_urls = {result["image_url"] for result in successes}
    print(f"successes={len(successes)}/{runs}")
    print(f"unique_image_urls={len(unique_urls)}")
    print(f"preview={successes[-1]['preview_path']}")
    print(f"metadata={meta_path}")
    return 0 if len(successes) == runs and len(unique_urls) == 1 else 2


def probe_once(out_dir: Path, index: int):
    html_text = fetch_text(PAGE_URL)
    parser = CandidateParser(PAGE_URL)
    parser.feed(html_text)

    candidates = collect_candidates(parser, html_text)
    errors = []
    for candidate in candidates[:30]:
        try:
            image = download_image(candidate["url"])
            if not looks_usable(image):
                continue
            preview = render_preview(image)
            preview_path = out_dir / f"natgeo_potd_{index + 1}.png"
            preview.save(preview_path)
            return {
                "status": "ok",
                "page_url": PAGE_URL,
                "page_title": parser.title,
                "image_url": candidate["url"],
                "candidate_score": candidate["score"],
                "source": candidate["source"],
                "image_size": image.size,
                "preview_path": str(preview_path),
            }
        except Exception as exc:
            errors.append({"url": candidate["url"], "error": str(exc)})

    return {
        "status": "failed",
        "page_url": PAGE_URL,
        "page_title": parser.title,
        "candidate_count": len(candidates),
        "errors": errors[-8:],
    }


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read()
    return data.decode("utf-8", "replace")


def collect_candidates(parser: CandidateParser, html_text: str):
    raw_candidates = []
    for source, items in [("meta", parser.meta), ("img", parser.images), ("link", parser.links)]:
        for item in items:
            raw_candidates.append({"url": clean_url(item["url"]), "label": item["label"], "source": source})

    for url in re.findall(r"https?:\\?/\\?/[^\"'<>\\s]+", html_text):
        cleaned = clean_url(url)
        if "natgeofe.com" in cleaned or "nationalgeographic.com" in cleaned:
            raw_candidates.append({"url": cleaned, "label": "raw-html", "source": "raw"})

    deduped = {}
    for candidate in raw_candidates:
        url = candidate["url"]
        if not usable_url(url):
            continue
        candidate["score"] = score_candidate(candidate)
        existing = deduped.get(url)
        if not existing or candidate["score"] > existing["score"]:
            deduped[url] = candidate
    return sorted(deduped.values(), key=lambda item: item["score"], reverse=True)


def clean_url(url: str) -> str:
    url = html.unescape(url or "")
    url = url.replace("\\/", "/").replace("\\u002F", "/").replace("\\u0026", "&")
    url = url.strip().strip('"').strip("'")
    return url


def usable_url(url: str) -> bool:
    lower = url.lower()
    if not lower.startswith(("http://", "https://")):
        return False
    reject = ["logo", "favicon", "sprite", "avatar", "placeholder", "transparent", ".svg", ".gif"]
    if any(token in lower for token in reject):
        return False
    return any(token in lower for token in ["natgeofe.com", "nationalgeographic.com"])


def score_candidate(candidate):
    haystack = f"{candidate['url']} {candidate.get('label', '')} {candidate.get('source', '')}".lower()
    score = 0
    if "i.natgeofe.com" in haystack:
        score += 120
    if "photo-of-the-day" in haystack:
        score += 80
    if candidate["source"] == "meta":
        score += 60
    if candidate["source"] == "img":
        score += 35
    for token in ["image", "photo", "potd", "nationalgeographic"]:
        if token in haystack:
            score += 15
    for token in ["logo", "icon", "newsletter", "disney"]:
        if token in haystack:
            score -= 80
    width_match = re.search(r"(?:width|w)=(\d+)", haystack)
    if width_match:
        score += min(int(width_match.group(1)) // 80, 30)
    return score


def download_image(url: str) -> Image.Image:
    req = urllib.request.Request(url, headers=IMAGE_HEADERS)
    with urllib.request.urlopen(req, timeout=35) as response:
        image_path = ROOT / ".tmp" / "natgeo_potd_probe" / "download.tmp"
        image_path.write_bytes(response.read())
    with Image.open(image_path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def looks_usable(image: Image.Image) -> bool:
    width, height = image.size
    return width >= 300 and height >= 200 and width * height >= 160_000


def render_preview(image: Image.Image) -> Image.Image:
    dimensions = (800, 480)
    image = ImageOps.exif_transpose(image).convert("RGB")
    background = ImageOps.fit(image, dimensions, method=Image.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(radius=8))
    background = ImageEnhance.Color(background).enhance(0.45)
    background = Image.blend(background, Image.new("RGB", dimensions, (255, 255, 255)), 0.28)
    fitted = ImageOps.contain(image, dimensions, method=Image.LANCZOS)
    x = (dimensions[0] - fitted.width) // 2
    y = (dimensions[1] - fitted.height) // 2
    background.paste(fitted, (x, y))
    draw_natgeo_logo(background)
    return background


def draw_natgeo_logo(image: Image.Image) -> None:
    logo_path = find_logo_asset()
    if not logo_path.is_file():
        return
    draw_official_natgeo_logo(image, logo_path)


def find_logo_asset() -> Path:
    out_dir = ROOT / ".tmp" / "natgeo_potd_probe"
    for path in [
        out_dir / "provided_natgeo_logo.png",
        out_dir / "official_natgeo_logo.png",
    ]:
        if path.is_file():
            return path
    return out_dir / "provided_natgeo_logo.png"


def draw_official_natgeo_logo(image: Image.Image, logo_path: Path) -> None:
    margin = 22
    with Image.open(logo_path) as logo:
        logo = ImageOps.exif_transpose(logo).convert("RGBA")
    logo = ImageOps.contain(logo, (218, 64), method=Image.LANCZOS)
    x = margin
    y = image.height - margin - logo.height
    image.paste(logo, (x, y), logo)


if __name__ == "__main__":
    raise SystemExit(main())
