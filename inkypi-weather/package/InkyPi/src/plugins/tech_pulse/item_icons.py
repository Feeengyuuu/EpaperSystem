"""The mark beside each item on Tech Pulse, so every story, skill, model and repository reads apart.

- A Hacker News story: its site's icon, from Google's favicon service by the story's host. The
  site itself is never fetched: story hosts are whatever people post.
- An agent skill: the avatar of the GitHub owner of its source repository.
- A Hugging Face model: its author's avatar on the Hub.
- A GitHub repository: its owner's avatar, which the search API already names.

Only the hosts named here are ever asked. Icons are downloaded during a data refresh, inside the
time the page gives them, and kept on disk within ICON_BUDGET; a redraw reads what is kept. An
owner's or author's avatar address is asked once and kept (KEPT for a found one, NO_AVATAR for
"has none"), and a picture that failed to load rests for RESTED. An item without its icon gets a
lettered tile in a colour of its own (``monogram``), so every row still carries a mark.

On the page a mark stands without a plate (``on_page``): the white or black paper a logo was
drawn on is cut away and the logo fills the slot; one that would then be hard to see on the page
(a dark logo on the night page, a pale one by day) has its lightness turned, its hues kept. A
photo or a tile of a colour keeps its square, its corners rounded.
"""

from __future__ import annotations

import json
import logging
import re
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from runtime.refresh_contracts import TaskCancelled
from utils.cache_manager import CacheBudget
from utils.http_client import HttpClientError, HttpStatusError
from utils.picture_grounds import cut_flat_ground, light_on_dark
from utils.remote_thumbnails import MIN_SECONDS_PER_IMAGE, load_thumbnails
from utils.theme_utils import relative_luminance

logger = logging.getLogger(__name__)

FAVICON_HOST = "t3.gstatic.com"
FAVICON_URL = ("https://" + FAVICON_HOST + "/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL"
               "&url=https://{host}&size=64")
# github.com/<owner>.png answers with a redirect to the owner's picture on the avatar host; the
# avatar host's own /<owner> form answers some owners with GitHub's placeholder instead.
GITHUB_OWNER_PICTURE = "https://github.com/{owner}.png?size=64"
GITHUB_AVATAR_HOST = "avatars.githubusercontent.com"
HUB_AVATAR_APIS = ("https://huggingface.co/api/organizations/{name}/avatar",
                   "https://huggingface.co/api/users/{name}/avatar")
HUB_AVATAR_HOST = "cdn-avatars.huggingface.co"
HN_HOST = "news.ycombinator.com"

ICON_PIXELS = (64, 64)
ICON_BYTES = 512 * 1024
ICON_BUDGET = CacheBudget(21 * 86400, 96, 2 * 1024 * 1024)
LOOKUP_SECONDS = 6
LOOKUP_BYTES = 256 * 1024
KEPT, NO_AVATAR, RESTED = timedelta(days=30), timedelta(hours=12), timedelta(hours=2)
KEPT_ENTRIES = 96

_HOST = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]")
_GITHUB_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_HUB_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,94}[A-Za-z0-9])?")
_GITHUB_PICTURE = re.compile(r"/u/(\d{1,12})")
_HUB_PICTURE = re.compile(r"/v1/production/uploads/[\w./-]{1,200}\.(?:png|jpe?g|webp|gif)")
# Sites that give each author a host of their own: the author's label names the story's source.
_SHARED_SITES = ("github.io", "substack.com", "medium.com", "blogspot.com", "wordpress.com", "netlify.app",
                 "vercel.app", "pages.dev", "bearblog.dev", "neocities.org", "tumblr.com")
_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "ac", "gov", "edu"})
# A monogram tile's colours: quiet, apart from each other, white lettering readable on each.
MONOGRAM_COLOURS = ((37, 99, 180), (176, 68, 52), (22, 128, 104), (132, 78, 176), (176, 112, 20), (40, 122, 150),
                    (168, 60, 112), (84, 108, 44))

# An icon's ground is flat when at least GROUND_SHARE of its edge is within GROUND_TOLERANCE of one
# colour (a disc drawn to the icon's full width takes the rest of the edge). It is paper, and is cut
# away, when that colour is near grey (GROUND_CHROMA) and either light (white paper) or dark (black
# paper).
GROUND_TOLERANCE, GROUND_SHARE, GROUND_CHROMA = 30, 0.7, 24
GROUND_LIGHT, GROUND_DARK = 215, 40
# A rounded tile saved as a square leaves a corner of another colour outside each of its own: at
# most this share of the icon each, they count as the tile's paper too.
CORNER_SHARE = 0.02
# A drawn icon has few colours: FLAT_COLOURS of them (at 3 bits a channel) hold FLAT_SHARE of it.
# A photo has many, and keeps its frame.
FLAT_COLOURS, FLAT_SHARE = 8, 0.9
# What a cut must leave of the icon to count (a white mark on white paper leaves nothing).
MARK_MIN_SHARE = 0.015
# A mark cut from its paper is turned when the page would hide what leads in it. On a dark page
# (relative luminance under DARK_PAGE) that is its dark part (luma under DARK_LUMA): turned when it
# is at least DARK_SHARE of the mark and more than TURN_LEAD times its light part, which could
# otherwise carry the mark alone (a white cat on a black disc). On a light page it is its pale part
# (luma over PALE_LUMA and near grey, under GREY_CHROMA: a brand's own light colour stays): turned
# when it is at least PALE_SHARE of the mark and more than TURN_LEAD times its dark part. The part
# weighed against is counted a pixel in from the mark's outline, where a cut leaves a rim of the
# paper's colour.
DARK_PAGE, DARK_LUMA, PALE_LUMA, GREY_CHROMA = 0.2, 64, 175, 48
DARK_SHARE, PALE_SHARE, TURN_LEAD = 0.12, 0.5, 2.0


@dataclass(frozen=True)
class Wanted:
    """An item's icon: the key the page finds it by, its picture's address when the feed already
    names it, and where its address can be asked (("github", owner) or ("hub", author))."""

    key: str
    url: str = ""
    lookup: tuple = ()


# --- whose icon ------------------------------------------------------------------------------

def site_host(story):
    """The host a story links to ("" for a post on Hacker News itself or an unusable one)."""
    host = str((story or {}).get("domain") or "").strip().lower().rstrip(".")
    host = host[4:] if host.startswith("www.") else host
    return host if host != HN_HOST and _HOST.fullmatch(host) else ""


def story_key(story):
    host = site_host(story)
    return f"site:{host}" if host else ""


def _owner(value):
    return str(value or "").strip().split("/", 1)[0].strip()


def github_owner(value):
    """The owner of an "owner/repository" name when GitHub could have it, else ""."""
    owner = _owner(value)
    return owner if _GITHUB_NAME.fullmatch(owner) else ""


def owner_key(value):
    """The key of a skill's or repository's owner: any owner has one (for its lettered tile)."""
    owner = _owner(value)
    return f"github:{owner.lower()[:40]}" if owner else ""


def _author(model):
    model = model if isinstance(model, dict) else {}
    return str(model.get("author") or _owner(model.get("id"))).strip()


def hub_author(model):
    """A model's author when the Hub could have it, else ""."""
    author = _author(model)
    return author if _HUB_NAME.fullmatch(author) else ""


def author_key(model):
    author = _author(model)
    return f"hub:{author[:96]}" if author else ""


def favicon_url(host):
    return FAVICON_URL.format(host=host) if host and _HOST.fullmatch(host) else ""


def github_avatar(url):
    """A GitHub avatar's address at the size the page keeps, or "" for anything else."""
    parts = urlsplit(str(url or ""))
    found = _GITHUB_PICTURE.fullmatch(parts.path)
    if parts.scheme != "https" or parts.netloc != GITHUB_AVATAR_HOST or not found:
        return ""
    return f"https://{GITHUB_AVATAR_HOST}/u/{found.group(1)}?s=64"


def hub_avatar(url):
    """A Hub avatar's address on its picture host, or "" (a generated default avatar has none)."""
    url = str(url or "")
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != HUB_AVATAR_HOST or parts.query or parts.fragment:
        return ""
    return url if _HUB_PICTURE.fullmatch(parts.path) else ""


def safe_icon_url(url):
    """``url`` when it is one of the addresses this module builds, else None."""
    url = str(url or "")
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.netloc == FAVICON_HOST and parts.path == "/faviconV2":
        return url
    return github_avatar(url) or hub_avatar(url) or None


def wanted(stories=(), skills=(), models=(), repos=()):
    """The icons of the items given (the rows the page draws), each key once, in the order they
    matter when time is short: stories, repositories, models, skills."""
    found = {}

    def add(key, url="", lookup=()):
        if key and key not in found:
            found[key] = Wanted(key, url, lookup)

    for story in stories:
        add(story_key(story), favicon_url(site_host(story)))
    for repo in repos:
        repo = repo if isinstance(repo, dict) else {}
        owner = github_owner(repo.get("full_name"))
        add(owner_key(repo.get("full_name")), github_avatar(repo.get("owner_avatar_url")),
            ("github", owner) if owner else ())
    for model in models:
        author = hub_author(model)
        add(author_key(model), lookup=("hub", author) if author else ())
    for skill in skills:
        source = (skill if isinstance(skill, dict) else {}).get("source")
        owner = github_owner(source)
        add(owner_key(source), lookup=("github", owner) if owner else ())
    return list(found.values())


# --- fetching and keeping ------------------------------------------------------------------

def _read(path):
    try:
        kept = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    kept = kept if isinstance(kept, dict) else {}
    return {name: dict(kept[name]) if isinstance(kept.get(name), dict) else {} for name in ("addresses", "missed")}


def _fresh(stamp, now, span):
    try:
        return timedelta(0) <= now - datetime.fromisoformat(str(stamp)) <= span
    except (TypeError, ValueError):
        return False


def _kept_address(kept, lookup, now):
    """(address, whether the answer still stands) for an owner or author asked before."""
    entry = kept["addresses"].get(":".join(lookup))
    if not isinstance(entry, dict):
        return "", False
    address = safe_icon_url(entry.get("url")) or ""
    return address, _fresh(entry.get("at"), now, KEPT if address else NO_AVATAR)


def _look_up(lookup, http, context, user_agent):
    """(address, answered): the avatar's address ("" when the owner or author has none), and
    whether the source answered at all (a failure is asked again next refresh)."""
    kind, name = lookup
    headers = {"User-Agent": user_agent}
    try:
        if kind == "github":
            result = http.request_bytes("GET", GITHUB_OWNER_PICTURE.format(owner=quote(name, safe="")),
                                        context=context, timeout=LOOKUP_SECONDS, max_bytes=LOOKUP_BYTES, headers=headers)
            return github_avatar(result.url), True
        for api in HUB_AVATAR_APIS:
            try:
                result = http.request_json("GET", api.format(name=quote(name, safe="")), context=context,
                                           timeout=LOOKUP_SECONDS, max_bytes=LOOKUP_BYTES, headers=headers)
            except HttpStatusError as error:
                if error.status == 404:
                    continue
                raise
            return hub_avatar(result.data.get("avatarUrl") if isinstance(result.data, dict) else ""), True
        return "", True
    except TaskCancelled:
        raise
    except HttpStatusError as error:
        return "", error.status == 404
    except (HttpClientError, OSError, ValueError) as error:
        logger.info("Tech Pulse avatar lookup failed: %s", type(error).__name__)
        return "", False


def load(wants, directory, lookups, *, http, context, cached_only, user_agent, now=None):
    """{key: RGBA icon} for the ``wants`` kept in ``directory`` or fetched in time; the avatar
    addresses asked, and the pictures that failed, are kept in the file ``lookups``. With
    ``cached_only`` nothing is asked, downloaded or written."""
    directory, lookups = Path(directory), Path(lookups)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    kept = _read(lookups)
    addresses, changed = {}, False
    try:
        for want in wants:
            url = want.url
            if not url and want.lookup:
                url, stands = _kept_address(kept, want.lookup, now)
                if not stands and not cached_only and context.remaining_seconds() >= LOOKUP_SECONDS:
                    found, answered = _look_up(want.lookup, http, context, user_agent)
                    if answered:
                        url = found
                        kept["addresses"][":".join(want.lookup)] = {"url": found, "at": now.isoformat()}
                        changed = True
            if url and (cached_only or not _fresh(kept["missed"].get(url), now, RESTED)):
                addresses[want.key] = url
        urls = list(dict.fromkeys(addresses.values()))
        images = load_thumbnails(urls, directory, http=http, context=context, cached_only=cached_only,
                                 safe_url=safe_icon_url, user_agent=user_agent, size=ICON_PIXELS, budget=ICON_BUDGET,
                                 keep_alpha=True, max_bytes=ICON_BYTES) if urls else {}
        if not cached_only and context.remaining_seconds() >= MIN_SECONDS_PER_IMAGE:
            # Time was left, so a picture still missing failed: it rests for a while.
            for url in urls:
                if url not in images:
                    kept["missed"][url] = now.isoformat()
                    changed = True
    finally:
        if changed and not cached_only:
            _write(lookups, kept, now)
    return {key: images[url] for key, url in addresses.items() if url in images}


def _write(path, kept, now):
    kept["missed"] = {url: stamp for url, stamp in kept["missed"].items() if _fresh(stamp, now, RESTED)}
    for name in ("addresses", "missed"):
        while len(kept[name]) > KEPT_ENTRIES:
            kept[name].pop(next(iter(kept[name])))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError as error:
        logger.warning("Tech Pulse could not keep its icon lookups: %s", error)


# --- on the page -----------------------------------------------------------------------------

def _solid(art):
    """255 where the picture is solid, else 0."""
    return art.getchannel("A").point(lambda value: 255 if value > 128 else 0)


def _solid_count(art):
    return _solid(art).histogram()[255]


def _paper_ground(flat):
    """The colour of the opaque icon's edge when it is one flat colour of paper (near grey, and
    light or dark), else None."""
    rgb = flat.convert("RGB")
    width, height = rgb.size
    pixels = rgb.load()
    edge = [pixels[x, y] for x in range(width) for y in (0, height - 1)]
    edge += [pixels[x, y] for y in range(1, height - 1) for x in (0, width - 1)]
    ground = tuple(sorted(channel)[len(channel) // 2] for channel in zip(*edge))
    near = sum(max(abs(a - b) for a, b in zip(pixel, ground)) <= GROUND_TOLERANCE for pixel in edge)
    if near < GROUND_SHARE * len(edge) or max(ground) - min(ground) > GROUND_CHROMA:
        return None
    return ground if min(ground) >= GROUND_LIGHT or max(ground) <= GROUND_DARK else None


def _few_colours(flat):
    counts = sorted((count for count, _colour in ImageOps.posterize(flat.convert("RGB"), 3).getcolors(512)),
                    reverse=True)
    return sum(counts[:FLAT_COLOURS]) >= FLAT_SHARE * sum(counts)


def _without_square_corners(flat, ground):
    """``flat`` with each small corner of another colour (CORNER_SHARE) given the ground's."""
    width, height = flat.size
    most = CORNER_SHARE * width * height
    for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        colour = flat.getpixel(corner)[:3]
        if max(abs(a - b) for a, b in zip(colour, ground)) <= GROUND_TOLERANCE:
            continue
        # From the corner up to the tile itself, the soft line between them included.
        reach = sum(abs(a - b) for a, b in zip(colour, ground)) - 3 * GROUND_TOLERANCE
        filled = flat.copy()
        ImageDraw.floodfill(filled, corner, (*ground, 255), thresh=max(0, reach))
        changed = ImageChops.difference(filled.convert("RGB"), flat.convert("RGB")).convert("L")
        if 0 < changed.point(lambda value: 255 if value else 0).histogram()[255] <= most:
            flat = filled
    return flat


def _alone(art):
    """The mark without the white or black paper it stands on, cropped to itself (RGBA); None when
    the icon is a tile of its own: a photo, or a square of a colour."""
    least = max(4, MARK_MIN_SHARE * art.width * art.height)
    for paper in ((255, 255, 255), (0, 0, 0)):
        # Whatever is clear in the icon counts as this paper (a mark drawn on nothing).
        flat = Image.new("RGBA", art.size, (*paper, 255))
        flat.alpha_composite(art)
        ground = _paper_ground(flat)
        if ground is None or not _few_colours(flat):
            continue
        cut = cut_flat_ground(_without_square_corners(flat, ground), max(art.size), tolerance=GROUND_TOLERANCE,
                              share=GROUND_SHARE)
        if cut is not None and _solid_count(cut) >= least:
            return cut
    alpha = art.getchannel("A")
    if alpha.getextrema()[0] >= 128:
        return None
    box = alpha.point(lambda value: 255 if value > 16 else 0).getbbox()
    return art.crop(box) if box else None


def _parts(mark):
    """(dark, pale, inner dark, inner light): the shares of the mark's solid pixels that are dark,
    and light and near grey; then those dark, and those light, a pixel in from its outline."""
    solid = _solid(mark)
    luma = mark.convert("L")
    counts = luma.histogram(mask=solid)
    total = sum(counts)
    if not total:
        return 0.0, 0.0, 0.0, 0.0
    red, green, blue = mark.convert("RGB").split()
    chroma = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(red, green), blue),
                                 ImageChops.darker(ImageChops.darker(red, green), blue))
    grey = ImageChops.multiply(solid, chroma.point(lambda value: 255 if value < GREY_CHROMA else 0))
    pale = sum(luma.histogram(mask=grey)[PALE_LUMA + 1:])
    inner = luma.histogram(mask=solid.filter(ImageFilter.MinFilter(3)))
    return (sum(counts[:DARK_LUMA]) / total, pale / total, sum(inner[:DARK_LUMA]) / total,
            sum(inner[PALE_LUMA + 1:]) / total)


def _hard_to_see(mark, paper):
    dark, pale, inner_dark, inner_light = _parts(mark)
    if relative_luminance(tuple(int(value) for value in paper[:3])) < DARK_PAGE:
        return dark >= DARK_SHARE and dark > TURN_LEAD * inner_light
    return pale >= PALE_SHARE and pale > TURN_LEAD * inner_dark


def _rounded(size, scale=4):
    width, height = size
    mask = Image.new("L", (width * scale, height * scale), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width * scale - 1, height * scale - 1),
                                           radius=min(width, height) * scale // 5, fill=255)
    return mask.resize((width, height), Image.Resampling.LANCZOS)


def on_page(icon, size, paper):
    """``icon`` as a ``size`` px RGBA tile for a page of ``paper``, without a plate behind it (see
    the module's note)."""
    art = icon.convert("RGBA")
    size = int(size)
    mark = _alone(art)
    if mark is None:
        fitted = ImageOps.contain(art, (size, size), method=Image.Resampling.LANCZOS)
        fitted.putalpha(ImageChops.multiply(fitted.getchannel("A"), _rounded(fitted.size)))
    else:
        if _hard_to_see(mark, paper):
            mark = Image.merge("RGBA", (*light_on_dark(mark.convert("RGB")).split(), mark.getchannel("A")))
        fitted = ImageOps.contain(mark, (size, size), method=Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return tile


def initial(key):
    """The letter a key's monogram shows: a site's own label, an owner's or author's first letter."""
    kind, _colon, name = str(key or "").partition(":")
    if kind == "site":
        labels = name.split(".")
        shared = next((site for site in _SHARED_SITES if name.endswith("." + site)), "")
        if shared:
            labels = name[: -len(shared) - 1].split(".")[-1:]
        elif len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
            labels = labels[-3:-2]
        else:
            labels = labels[-2:-1]
        name = labels[0] if labels else name
    return next((char.upper() for char in name if char.isalnum()), "?")


def monogram(key, size, font):
    """The stand-in for an icon not at hand: the key's initial in white on a rounded tile, in one
    of MONOGRAM_COLOURS chosen by the key, so the same source always gets the same tile."""
    size = int(size)
    colour = MONOGRAM_COLOURS[zlib.crc32(str(key).encode("utf-8")) % len(MONOGRAM_COLOURS)]
    tile = Image.new("RGBA", (size, size), (*colour, 255))
    ImageDraw.Draw(tile).text((size / 2, size / 2), initial(key), font=font, fill=(255, 255, 255), anchor="mm")
    tile.putalpha(_rounded((size, size)))
    return tile
