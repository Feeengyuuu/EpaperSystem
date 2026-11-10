"""Tech Pulse's item icons (plugins/tech_pulse/item_icons.py): whose icon, where it may come from,
how it is kept, how it stands on the page, and how the page draws it."""

import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image, ImageDraw
import pytest

try:
    import psutil  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault(
        "psutil",
        SimpleNamespace(
            virtual_memory=lambda: SimpleNamespace(total=2 * 1024**3),
            swap_memory=lambda: SimpleNamespace(percent=0.0),
        ),
    )
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.tech_pulse import item_icons  # noqa: E402
from plugins.tech_pulse import tech_pulse as tech_pulse_module  # noqa: E402
from plugins.tech_pulse.item_icons import Wanted  # noqa: E402
from plugins.tech_pulse.tech_pulse import HN_DOCS_URL, TechPulse  # noqa: E402
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded  # noqa: E402
from utils.app_utils import get_base_ui_font  # noqa: E402
from utils.http_client import HttpClientError, HttpStatusError  # noqa: E402
from utils.remote_thumbnails import _key as kept_name  # noqa: E402

NOW = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)
AGENT = "InkyPi TechPulse/test"
NIGHT_PAPER, DAY_PAPER = (0, 0, 0), (255, 251, 241)
ANTHROPIC = item_icons.FAVICON_URL.format(host="anthropic.com")
USPS = item_icons.FAVICON_URL.format(host="postalpro.usps.com")
MORLUTO = "https://avatars.githubusercontent.com/u/123?s=64"
VERCEL_ASK, VERCEL = "https://github.com/vercel-labs.png?size=64", "https://avatars.githubusercontent.com/u/108547162?s=64"
ACME_ORG = "https://huggingface.co/api/organizations/acme/avatar"
ACME_USER = "https://huggingface.co/api/users/acme/avatar"
ACME = "https://cdn-avatars.huggingface.co/v1/production/uploads/6828d5f2/X6_QZmbo.png"
ALLOWED_HOSTS = {"t3.gstatic.com", "github.com", "avatars.githubusercontent.com", "huggingface.co",
                 "cdn-avatars.huggingface.co"}


def _png(colour, size=(48, 48)):
    buffer = BytesIO()
    Image.new("RGBA", size, colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _context(seconds=30):
    return TaskContext(threading.Event(), time.monotonic() + seconds, time.monotonic)


class _Web:
    """A stand-in for the shared HTTP client: answers by URL from ``pages`` (bytes, JSON, an error
    to raise, or (final URL, bytes) for a redirect followed), 404 for the rest; keeps what was asked."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def _answer(self, url, kwargs):
        self.asked.append((url, kwargs))
        answer = self.pages.get(url)
        if answer is None:
            raise HttpStatusError("GET", url, 404)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def request_bytes(self, method, url, **kwargs):
        answer = self._answer(url, kwargs)
        final, data = answer if isinstance(answer, tuple) else (url, answer)
        return SimpleNamespace(status=200, data=data, headers={}, url=final)

    def request_json(self, method, url, **kwargs):
        return SimpleNamespace(status=200, data=self._answer(url, kwargs), headers={}, url=url)

    @property
    def urls(self):
        return [url for url, _kwargs in self.asked]


def _web(**extra):
    return _Web({
        ANTHROPIC: _png((10, 20, 200, 255)),
        MORLUTO: _png((200, 30, 30, 255)),
        VERCEL_ASK: ("https://avatars.githubusercontent.com/u/108547162", _png((0, 0, 0, 255))),
        VERCEL: _png((30, 160, 60, 255)),
        ACME_USER: {"avatarUrl": ACME},
        ACME: _png((240, 200, 20, 255)),
        **extra,
    })


WANTS = [
    Wanted("site:anthropic.com", ANTHROPIC),
    Wanted("github:morluto", MORLUTO, ("github", "morluto")),
    Wanted("hub:acme", "", ("hub", "acme")),
    Wanted("github:vercel-labs", "", ("github", "vercel-labs")),
]


def _load(tmp_path, web, wants=WANTS, *, now=NOW, cached_only=False, context=None):
    return item_icons.load(wants, tmp_path / "icons", tmp_path / "icon-lookups.json", http=web,
                           context=context or _context(), cached_only=cached_only, user_agent=AGENT, now=now)


# --- whose icon ------------------------------------------------------------------------------

def test_each_item_names_one_icon_from_a_known_host():
    stories = [{"domain": "anthropic.com"}, {"domain": "news.ycombinator.com"}, {"domain": "WWW.PostalPro.usps.com"},
               {"domain": "anthropic.com"}, {"domain": "localhost:3000"}, {"domain": "192.168.0.1"}]
    skills = [{"name": "find-skills", "source": "vercel-labs/skills"},
              {"name": "agent-browser", "source": "vercel-labs/agent-browser"}, {"name": "odd", "source": ""}]
    models = [{"id": "acme/tiny-ocr", "author": "acme"}, {"id": "gpt2"}]
    repos = [{"full_name": "morluto/rea", "owner_avatar_url": "https://avatars.githubusercontent.com/u/123?v=4"},
             {"full_name": "stablyai/orca", "owner_avatar_url": "https://evil.example/u/9"}]

    wants = item_icons.wanted(stories, skills, models, repos)

    # Stories first, then repositories, models and skills; each owner once.
    assert wants == [
        Wanted("site:anthropic.com", ANTHROPIC),
        Wanted("site:postalpro.usps.com", USPS),
        Wanted("github:morluto", MORLUTO, ("github", "morluto")),
        Wanted("github:stablyai", "", ("github", "stablyai")),
        Wanted("hub:acme", "", ("hub", "acme")),
        Wanted("hub:gpt2", "", ("hub", "gpt2")),
        Wanted("github:vercel-labs", "", ("github", "vercel-labs")),
    ]
    assert all(item_icons.safe_icon_url(want.url) == want.url for want in wants if want.url)
    # A post on Hacker News itself, and a host no favicon can be asked for, have no key of a site.
    assert item_icons.story_key(stories[1]) == "" and item_icons.story_key(stories[4]) == ""


def test_an_owner_or_author_always_has_a_key_but_is_only_asked_when_its_name_is_plain():
    assert item_icons.owner_key("we_ird!/repo") == "github:we_ird!" and item_icons.github_owner("we_ird!/repo") == ""
    assert item_icons.author_key({"id": "sp ace/model"}) == "hub:sp ace" and item_icons.hub_author({"id": "sp ace/m"}) == ""
    assert item_icons.owner_key("") == "" and item_icons.author_key({}) == ""

    wants = item_icons.wanted(skills=[{"source": "we_ird!/repo"}], models=[{"id": "sp ace/model"}])

    assert wants == [Wanted("hub:sp ace"), Wanted("github:we_ird!")]


@pytest.mark.parametrize("url", [ANTHROPIC, MORLUTO, ACME])
def test_the_modules_own_addresses_are_safe(url):
    assert item_icons.safe_icon_url(url) == url


@pytest.mark.parametrize("url", [
    ANTHROPIC.replace("https://", "http://"),
    "https://t3.gstatic.com@evil.example/faviconV2?url=https://anthropic.com",
    "https://t3.gstatic.com:8443/faviconV2?url=https://anthropic.com",
    "https://t3.gstatic.com/other?url=https://anthropic.com",
    "https://avatars.githubusercontent.com/vercel-labs?s=64",
    "https://avatars.githubusercontent.com.evil.example/u/1",
    "https://cdn-avatars.huggingface.co/v1/production/uploads/a/b.svg",
    "https://cdn-avatars.huggingface.co/v1/production/uploads/a/b.png?redirect=https://evil.example",
    "https://huggingface.co/avatars/abc.svg",
    "https://example.com/favicon.ico",
    "",
    None,
])
def test_any_other_address_is_refused(url):
    assert item_icons.safe_icon_url(url) is None


def test_a_github_avatar_is_kept_at_the_size_the_page_uses():
    assert item_icons.github_avatar("https://avatars.githubusercontent.com/u/6216945?v=4") == \
        "https://avatars.githubusercontent.com/u/6216945?s=64"
    assert item_icons.github_avatar("https://avatars.githubusercontent.com/u/6216945") == \
        "https://avatars.githubusercontent.com/u/6216945?s=64"
    assert item_icons.github_avatar("https://avatars.githubusercontent.com/u/6216945/../x") == ""


# --- fetching and keeping ------------------------------------------------------------------

def test_a_refresh_asks_the_known_hosts_once_and_a_redraw_reads_what_is_kept(tmp_path):
    web = _web()

    icons = _load(tmp_path, web)

    assert set(icons) == {"site:anthropic.com", "github:morluto", "hub:acme", "github:vercel-labs"}
    assert all(icon.mode == "RGBA" for icon in icons.values())
    assert icons["site:anthropic.com"].getpixel((5, 5)) == (10, 20, 200, 255)
    assert icons["github:vercel-labs"].getpixel((5, 5)) == (30, 160, 60, 255)
    # The Hub is asked for an organisation, then for a user; GitHub by the owner's picture, whose
    # redirect names the avatar. Nothing else is asked, and every request names the page.
    assert web.urls == [ACME_ORG, ACME_USER, VERCEL_ASK, ANTHROPIC, MORLUTO, ACME, VERCEL]
    assert {url.split("/")[2] for url in web.urls} <= ALLOWED_HOSTS
    assert all(kwargs["headers"]["User-Agent"] == AGENT for _url, kwargs in web.asked)
    kept = json.loads((tmp_path / "icon-lookups.json").read_text(encoding="utf-8"))
    assert kept["addresses"]["hub:acme"]["url"] == ACME and kept["addresses"]["github:vercel-labs"]["url"] == VERCEL
    assert kept["missed"] == {}
    before = sorted(path.name for path in tmp_path.rglob("*") if path.is_file())

    # A redraw: no client at all, the same icons, nothing written.
    again = _load(tmp_path, None, cached_only=True)

    assert set(again) == set(icons) and again["hub:acme"].getpixel((5, 5)) == (240, 200, 20, 255)
    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == before

    # The next refresh finds everything kept and asks nothing.
    quiet = _web()
    assert set(_load(tmp_path, quiet, now=NOW + timedelta(hours=1))) == set(icons)
    assert quiet.urls == []


def test_a_kept_avatar_address_is_asked_again_after_a_month(tmp_path):
    _load(tmp_path, _web())
    later = _web()

    _load(tmp_path, later, now=NOW + item_icons.KEPT + timedelta(hours=1))

    assert later.urls == [ACME_ORG, ACME_USER, VERCEL_ASK]


def test_a_picture_that_fails_rests_before_it_is_asked_again(tmp_path):
    wants = [Wanted("site:anthropic.com", ANTHROPIC), Wanted("site:postalpro.usps.com", USPS)]
    web = _web()

    # The favicon service has no icon for the second site (404).
    icons = _load(tmp_path, web, wants)

    assert set(icons) == {"site:anthropic.com"} and web.urls == [ANTHROPIC, USPS]
    kept = json.loads((tmp_path / "icon-lookups.json").read_text(encoding="utf-8"))
    assert list(kept["missed"]) == [USPS]
    # Inside the rest it is left alone; after it, asked again (and found).
    resting = _web()
    assert set(_load(tmp_path, resting, wants, now=NOW + timedelta(minutes=30))) == {"site:anthropic.com"}
    assert resting.urls == []
    back = _web(**{USPS: _png((0, 0, 90, 255))})
    assert set(_load(tmp_path, back, wants, now=NOW + item_icons.RESTED + timedelta(minutes=1))) == \
        {"site:anthropic.com", "site:postalpro.usps.com"}
    assert back.urls == [USPS]


def test_an_owner_without_an_avatar_is_remembered_and_a_failed_lookup_is_asked_again(tmp_path):
    wants = [Wanted("github:ghost", "", ("github", "ghost")), Wanted("hub:nobody", "", ("hub", "nobody")),
             Wanted("github:flaky", "", ("github", "flaky"))]
    web = _web(**{"https://github.com/flaky.png?size=64": HttpClientError("transport failed")})

    assert _load(tmp_path, web, wants) == {}

    kept = json.loads((tmp_path / "icon-lookups.json").read_text(encoding="utf-8"))["addresses"]
    # GitHub and the Hub said "no such owner"; the failed lookup is not kept.
    assert kept["github:ghost"]["url"] == "" and kept["hub:nobody"]["url"] == "" and "github:flaky" not in kept
    again = _web(**{"https://github.com/flaky.png?size=64": ("https://avatars.githubusercontent.com/u/7", _png((1, 2, 3, 255))),
                    "https://avatars.githubusercontent.com/u/7?s=64": _png((1, 2, 3, 255))})
    icons = _load(tmp_path, again, wants, now=NOW + timedelta(hours=1))
    assert set(icons) == {"github:flaky"}
    assert again.urls == ["https://github.com/flaky.png?size=64", "https://avatars.githubusercontent.com/u/7?s=64"]
    # After NO_AVATAR the two without one are asked again.
    later = _web()
    _load(tmp_path, later, wants[:2], now=NOW + item_icons.NO_AVATAR + timedelta(minutes=1))
    assert later.urls == ["https://github.com/ghost.png?size=64",
                          "https://huggingface.co/api/organizations/nobody/avatar",
                          "https://huggingface.co/api/users/nobody/avatar"]


def test_an_avatar_served_from_anywhere_else_is_not_used(tmp_path):
    wants = [Wanted("hub:acme", "", ("hub", "acme")), Wanted("github:vercel-labs", "", ("github", "vercel-labs"))]
    web = _web(**{ACME_USER: {"avatarUrl": "https://evil.example/a.png"},
                  VERCEL_ASK: ("https://evil.example/u/108547162", _png((0, 0, 0, 255)))})

    assert _load(tmp_path, web, wants) == {}
    assert web.urls == [ACME_ORG, ACME_USER, VERCEL_ASK]


def test_with_no_time_left_nothing_is_asked_and_nothing_is_blamed(tmp_path):
    web = _web()

    assert _load(tmp_path, web, context=_context(seconds=2)) == {}

    assert web.urls == [] and not (tmp_path / "icon-lookups.json").exists()


def test_a_cancelled_refresh_passes_on_and_keeps_what_it_learned(tmp_path):
    web = _web(**{ANTHROPIC: TaskCancelled("stop")})

    with pytest.raises(TaskCancelled):
        _load(tmp_path, web)

    kept = json.loads((tmp_path / "icon-lookups.json").read_text(encoding="utf-8"))
    assert kept["addresses"]["hub:acme"]["url"] == ACME and kept["missed"] == {}


def test_a_spoiled_lookups_file_is_read_as_empty(tmp_path):
    (tmp_path / "icon-lookups.json").write_text('{"addresses": [1, 2], "missed": "x"', encoding="utf-8")
    web = _web()

    assert "hub:acme" in _load(tmp_path, web)
    assert json.loads((tmp_path / "icon-lookups.json").read_text(encoding="utf-8"))["addresses"]["hub:acme"]["url"] == ACME


# --- on the page -----------------------------------------------------------------------------

def _drawn(ground, ink, *, shape="rectangle", size=64):
    icon = Image.new("RGBA", (size, size), ground)
    draw = ImageDraw.Draw(icon)
    box = (size * 5 // 16, size * 3 // 16, size * 11 // 16, size * 13 // 16)
    (draw.ellipse if shape == "ellipse" else draw.rectangle)(box, fill=ink)
    return icon


def _luma(pixel):
    return 0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2]


def test_a_black_mark_on_white_paper_stands_alone_and_turns_light_on_the_night_page():
    icon = _drawn((255, 255, 255, 255), (0, 0, 0, 255), shape="ellipse")

    night = item_icons.on_page(icon, 40, NIGHT_PAPER)
    day = item_icons.on_page(icon, 40, DAY_PAPER)

    for tile in (night, day):
        assert tile.size == (40, 40) and tile.mode == "RGBA"
        # No plate: the paper is gone from the corners, and the mark fills the slot's height.
        assert tile.getpixel((0, 0))[3] == 0 and tile.getpixel((39, 39))[3] == 0
        assert tile.getpixel((20, 2))[3] == 255 and tile.getpixel((20, 37))[3] == 255
    assert _luma(night.getpixel((20, 20))) > 230 and _luma(day.getpixel((20, 20))) < 25


def test_a_white_mark_on_a_black_tile_loses_the_tile_and_turns_dark_by_day():
    icon = _drawn((0, 0, 0, 255), (255, 255, 255, 255), shape="ellipse")

    night = item_icons.on_page(icon, 30, NIGHT_PAPER)
    day = item_icons.on_page(icon, 30, DAY_PAPER)

    assert night.getpixel((0, 0))[3] == 0 and day.getpixel((0, 0))[3] == 0
    assert _luma(night.getpixel((15, 15))) > 230 and _luma(day.getpixel((15, 15))) < 25


def test_a_mark_drawn_on_nothing_is_treated_like_one_on_paper():
    dark = _drawn((0, 0, 0, 0), (20, 20, 20, 255), shape="ellipse")
    pale = _drawn((0, 0, 0, 0), (250, 250, 250, 255), shape="ellipse")

    assert _luma(item_icons.on_page(dark, 30, NIGHT_PAPER).getpixel((15, 15))) > 200
    assert _luma(item_icons.on_page(dark, 30, DAY_PAPER).getpixel((15, 15))) < 40
    assert _luma(item_icons.on_page(pale, 30, NIGHT_PAPER).getpixel((15, 15))) > 230
    assert _luma(item_icons.on_page(pale, 30, DAY_PAPER).getpixel((15, 15))) < 25


@pytest.mark.parametrize("paper", [NIGHT_PAPER, DAY_PAPER])
def test_a_brands_own_colour_is_never_turned(paper):
    green, pink = (118, 185, 0, 255), (240, 160, 160, 255)

    for colour in (green, pink):
        tile = item_icons.on_page(_drawn((255, 255, 255, 255), colour, shape="ellipse"), 40, paper)
        assert tile.getpixel((20, 20)) == colour and tile.getpixel((0, 0))[3] == 0


@pytest.mark.parametrize("paper", [NIGHT_PAPER, DAY_PAPER])
def test_a_tile_of_a_colour_keeps_its_square_with_rounded_corners(paper):
    orange = (255, 102, 0, 255)

    tile = item_icons.on_page(_drawn(orange, (255, 255, 255, 255)), 40, paper)

    assert tile.getpixel((20, 2)) == orange and tile.getpixel((2, 20)) == orange
    assert tile.getpixel((20, 20)) == (255, 255, 255, 255)
    assert tile.getpixel((0, 0))[3] == 0 and tile.getpixel((39, 0))[3] == 0


def test_a_photo_keeps_its_frame_even_with_a_white_border():
    photo = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    pixels = photo.load()
    for y in range(4, 60):
        for x in range(4, 60):
            pixels[x, y] = (x * 4, y * 4, (x + y) * 2, 255)

    tile = item_icons.on_page(photo, 40, NIGHT_PAPER)

    # The white border is still there (nothing was cut) and the picture is whole.
    assert item_icons._alone(photo) is None
    assert min(tile.getpixel((20, 1))[:3]) > 240 and tile.getpixel((20, 1))[3] == 255
    assert tile.getpixel((20, 20))[3] == 255


def test_a_disc_drawn_to_the_icons_full_width_is_still_cut_from_its_paper():
    icon = Image.new("RGBA", (32, 32), (255, 255, 255, 255))
    draw = ImageDraw.Draw(icon)
    draw.ellipse((0, 0, 31, 31), fill=(0, 0, 0, 255))
    draw.rectangle((10, 12, 21, 19), fill=(255, 255, 255, 255))

    night = item_icons.on_page(icon, 30, NIGHT_PAPER)
    day = item_icons.on_page(icon, 30, DAY_PAPER)

    # The white corners are gone on both pages. By day it is the black disc with its white mark; on
    # the night page the disc turns white and what is drawn on it dark.
    assert night.getpixel((0, 0))[3] == 0 and day.getpixel((0, 0))[3] == 0
    assert _luma(day.getpixel((15, 4))) < 25 and _luma(day.getpixel((15, 15))) > 230
    assert _luma(night.getpixel((15, 4))) > 230 and _luma(night.getpixel((15, 15))) < 25


@pytest.mark.parametrize("paper, light", [(NIGHT_PAPER, True), (DAY_PAPER, False)])
def test_a_rounded_tile_saved_as_a_square_leaves_no_corners_behind(paper, light):
    icon = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((0, 0, 63, 63), radius=9, fill=(22, 22, 24, 255))
    draw.ellipse((20, 12, 43, 51), fill=(255, 255, 255, 255))

    tile = item_icons.on_page(icon, 40, paper)

    # Only the mark is left, filling the slot: nothing of the tile or its white corners around it.
    solid = [(x, y) for y in range(40) for x in range(40) if tile.getpixel((x, y))[3] > 128]
    assert tile.getpixel((0, 0))[3] == 0 and tile.getpixel((39, 39))[3] == 0
    assert min(y for _x, y in solid) <= 1 and max(y for _x, y in solid) >= 38
    assert all((_luma(tile.getpixel(point)) > 128) == light for point in solid)


def test_initials_name_the_source():
    assert [item_icons.initial(key) for key in (
        "site:e-means.github.io", "site:postalpro.usps.com", "site:bbc.co.uk", "site:simonwillison.net",
        "site:someone.substack.com", "github:vercel-labs", "hub:moonshotai", "skill:x-ray", "item:?", "")] == \
        ["E", "U", "B", "S", "S", "V", "M", "X", "?", "?"]


def test_a_lettered_tile_stands_in_with_the_same_colour_for_the_same_source():
    font = get_base_ui_font(12, bold=True)

    tile = item_icons.monogram("github:vercel-labs", 20, font)
    again = item_icons.monogram("github:vercel-labs", 20, font)

    assert tile.size == (20, 20) and tile.mode == "RGBA" and tile.tobytes() == again.tobytes()
    assert tile.getpixel((0, 0))[3] == 0 and tile.getpixel((10, 1))[:3] in item_icons.MONOGRAM_COLOURS
    # The letter is white on it.
    assert any(pixel[:3] == (255, 255, 255) for pixel in tile.get_flattened_data())
    colours = {item_icons.monogram(f"site:{name}.com", 20, font).getpixel((10, 1))[:3]
               for name in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")}
    assert len(colours) >= 3


# --- the page --------------------------------------------------------------------------------

def _plugin(tmp_path):
    plugin = TechPulse({"id": "tech_pulse"})
    plugin._cache_dir = lambda create=True: tmp_path / "cache"
    plugin._data_dir = lambda create=True: tmp_path / "state"
    return plugin


def _theme(mode):
    palette = {"background": (12, 17, 28), "panel": (0, 0, 0), "ink": (255, 255, 255), "muted": (194, 196, 202),
               "rule": (46, 48, 56), "accent": (135, 159, 224)}
    return {"mode": mode, "palette": palette} if mode == "night" else {"mode": "day"}


def _payload(plugin):
    items = [(1, "Lead story", "https://www.anthropic.com/news"), (2, "Ask HN: a post without a site", ""),
             (3, "A story on a site without an icon at hand", "https://blog.unknown-site.dev/post"),
             (4, "Fourth story", "https://postalpro.usps.com/zcb"), (5, "Fifth story, never drawn", "https://fifth.example.org/")]
    stories = []
    for rank, (story_id, title, url) in enumerate(items, start=1):
        story = plugin._parse_story_item({"id": story_id, "type": "story", "title": title, "url": url, "by": "alice",
                                          "time": 1782520200, "score": 100 * rank, "descendants": 20}, now=NOW)
        story["rank"] = rank
        stories.append(story)
    return plugin._build_payload("topstories", stories, "live", NOW, [HN_DOCS_URL])


def _boards():
    return {
        "skills": [
            {"rank": 1, "name": "hyperframes", "source": "heygen-com/hyperframes", "installs": 23700,
             "installs_display": "23.7K"},
            {"rank": 5, "name": "find-skills", "source": "vercel-labs/skills", "installs": 21100,
             "installs_display": "21.1K"},
        ],
        "models": [{"id": "acme/tiny-ocr", "author": "acme", "pipeline_tag": "image-text-to-text",
                    "trending_score": 2147, "likes": 2509, "downloads_30d": 57715}],
        "repos": [
            {"id": 1, "full_name": "morluto/rea", "stars": 371228, "language": "TypeScript", "stars_24h": 6530,
             "owner_avatar_url": "https://avatars.githubusercontent.com/u/123?v=4"},
            {"id": 2, "full_name": "stablyai/orca", "stars": 103426, "language": "TypeScript", "stars_24h": None},
        ],
        "status": {"aggregate": "LIVE", "sources": {"skills": "live", "huggingface": "live", "github": "live"}},
    }


MAGENTA, TEAL, LIME, VIOLET = (230, 0, 160, 255), (0, 170, 170, 255), (140, 220, 0, 255), (120, 60, 220, 255)


def _tracked(plugin, monkeypatch):
    marks, texts = [], []
    original_mark, original_text = plugin._mark, plugin._draw_text

    def mark(image, key, xy, size, palette, fonts, icons):
        marks.append((key, (round(xy[0]), round(xy[1])), size))
        return original_mark(image, key, xy, size, palette, fonts, icons)

    def text(draw, xy, value, font, fill, **kwargs):
        texts.append((str(value), draw.textbbox(xy, str(value), font=font, anchor=kwargs.get("anchor"))))
        return original_text(draw, xy, value, font, fill, **kwargs)

    monkeypatch.setattr(plugin, "_mark", mark)
    monkeypatch.setattr(plugin, "_draw_text", text)
    return marks, texts


@pytest.mark.parametrize("mode", ["night", "day"])
def test_the_page_draws_each_items_own_mark(tmp_path, monkeypatch, mode):
    plugin = _plugin(tmp_path)
    marks, texts = _tracked(plugin, monkeypatch)
    icons = {"site:anthropic.com": Image.new("RGBA", (48, 48), MAGENTA),
             "site:postalpro.usps.com": Image.new("RGBA", (48, 48), TEAL),
             "github:vercel-labs": Image.new("RGBA", (48, 48), LIME),
             "hub:acme": Image.new("RGBA", (48, 48), VIOLET)}

    page = plugin._render_page((800, 480), _payload(plugin), {"_inkypi_theme": _theme(mode)}, NOW, _boards(), {},
                               None, icons)

    module = tech_pulse_module
    assert [(key, size) for key, _xy, size in marks] == [
        ("site:anthropic.com", module.LEAD_ICON), ("", module.STORY_ICON),
        ("site:blog.unknown-site.dev", module.STORY_ICON), ("site:postalpro.usps.com", module.STORY_ICON),
        ("github:heygen-com", module.SKILL_ICON), ("github:vercel-labs", module.SKILL_ICON),
        ("hub:acme", module.BOARD_ICON), ("github:morluto", module.BOARD_ICON), ("github:stablyai", module.BOARD_ICON),
    ]
    centre = {key: page.getpixel((x + size // 2, y + size // 2)) for key, (x, y), size in marks}
    edge = {key: page.getpixel((x + size // 2, y + 1)) for key, (x, y), size in marks}
    # An icon at hand shows as it is.
    assert centre["site:anthropic.com"] == MAGENTA[:3] and centre["site:postalpro.usps.com"] == TEAL[:3]
    assert centre["github:vercel-labs"] == LIME[:3] and centre["hub:acme"] == VIOLET[:3]
    # One not at hand gets its lettered tile, and a post on Hacker News itself Hacker News' logo.
    for key in ("site:blog.unknown-site.dev", "github:heygen-com", "github:morluto", "github:stablyai"):
        assert edge[key] in item_icons.MONOGRAM_COLOURS, key
    red, green, blue = edge[""]
    assert red > 200 and 60 < green < 160 and blue < 60
    # Every item's name starts clear of its mark, on the mark's own row.
    names = {"site:anthropic.com": "Lead story", "": "Ask HN: a post without a site", "github:heygen-com": "hyperframes",
             "github:vercel-labs": "find-skills", "hub:acme": "acme/tiny-ocr", "github:morluto": "morluto/rea",
             "github:stablyai": "stablyai/orca"}
    boxes = dict(texts)
    for key, (x, y), size in marks:
        if key in names:
            left, top, _right, bottom = boxes[names[key]]
            assert left >= x + size + 4, key
            assert top < y + size and bottom > y, key


def test_a_storys_rank_rides_its_icons_corner_and_a_boards_rank_stands_before_its_icon(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    marks, texts = _tracked(plugin, monkeypatch)

    page = plugin._render_page((800, 480), _payload(plugin), {"_inkypi_theme": _theme("night")}, NOW, _boards(), {})

    orange = plugin._palette({"_inkypi_theme": _theme("night")})["orange"]
    module = tech_pulse_module
    for (key, (x, y), size), rank in zip(marks[:4], "1234"):
        # The disc overhangs the icon's upper left corner; its figure is drawn on it.
        assert page.getpixel((x - module.RANK_OVERHANG + 2, y + 3)) == orange, key
        left, top, right, bottom = next(box for value, box in texts if value == rank and box[0] < x + 12)
        assert x - module.RANK_OVERHANG <= left and right <= x - module.RANK_OVERHANG + module.STORY_RANK_DISC
        assert y - module.RANK_OVERHANG <= top and bottom <= y - module.RANK_OVERHANG + module.STORY_RANK_DISC
    for key, (x, y), size in marks[6:]:
        rank = next(box for value, box in texts if value in "123" and box[2] <= x and x - box[2] < 14
                    and box[1] < y + size and box[3] > y)
        assert rank[2] <= x, key


def test_a_theme_render_reads_kept_icons_and_touches_nothing(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    icons_dir = tmp_path / "cache" / "icons"
    icons_dir.mkdir(parents=True)
    (icons_dir / kept_name(ANTHROPIC, True)).write_bytes(_png(MAGENTA))
    (icons_dir / kept_name(MORLUTO, True)).write_bytes(_png(TEAL))
    monkeypatch.setattr(tech_pulse_module, "get_http_client",
                        lambda: (_ for _ in ()).throw(AssertionError("a theme render must not open a client")))
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))

    icons = plugin._icons(_payload(plugin), _boards(), cache_only=True)

    assert set(icons) == {"site:anthropic.com", "github:morluto"}
    assert icons["site:anthropic.com"].getpixel((3, 3)) == MAGENTA
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before

    # A cold cache: nothing to show, and nothing is created.
    cold = _plugin(tmp_path / "cold")
    assert cold._icons(_payload(cold), _boards(), cache_only=True) == {}
    assert not (tmp_path / "cold").exists()


def test_a_refresh_fetches_icons_only_for_the_rows_drawn(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    web = _web(**{USPS: _png(TEAL), "https://github.com/heygen-com.png?size=64":
                  ("https://avatars.githubusercontent.com/u/5", _png(LIME)),
                  "https://avatars.githubusercontent.com/u/5?s=64": _png(LIME)})
    monkeypatch.setattr(tech_pulse_module, "get_http_client", lambda: web)

    icons = plugin._icons(_payload(plugin), _boards(), cache_only=False)

    assert set(icons) == {"site:anthropic.com", "site:postalpro.usps.com", "github:morluto", "hub:acme",
                          "github:heygen-com", "github:vercel-labs"}
    # The fifth story is not drawn, so its site is never asked for; nor is any host but the known ones.
    assert not any("fifth.example.org" in url for url in web.urls)
    assert {url.split("/")[2] for url in web.urls} <= ALLOWED_HOSTS
    assert all(kwargs["headers"]["User-Agent"] == tech_pulse_module.USER_AGENT for _url, kwargs in web.asked)
    assert (tmp_path / "cache" / "icon-lookups.json").is_file() and (tmp_path / "cache" / "icons").is_dir()
    for icon in icons.values():
        icon.close()


def test_stand_in_stories_and_demo_boards_ask_for_no_icons(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    web = _web()
    monkeypatch.setattr(tech_pulse_module, "get_http_client", lambda: web)
    boards = _boards()
    boards["status"]["sources"] = {"skills": "fixture", "huggingface": "fixture", "github": "fixture"}
    sample = _payload(plugin)
    sample["status"]["source_state"] = "local_sample"

    assert plugin._icons(sample, boards, cache_only=False) == {}

    assert web.urls == []


@pytest.mark.parametrize("failure", [RuntimeError("no client"), OSError("disk"), HttpClientError("down")])
def test_icons_that_cannot_be_had_never_fail_the_page(tmp_path, monkeypatch, failure):
    plugin = _plugin(tmp_path)

    def broken(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(tech_pulse_module, "get_http_client", lambda: _web())
    monkeypatch.setattr(item_icons, "load", broken)

    assert plugin._icons(_payload(plugin), _boards(), cache_only=False) == {}


def test_the_icons_own_time_running_out_leaves_the_page_what_is_kept(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    calls = []
    kept = {"site:anthropic.com": Image.new("RGBA", (8, 8), MAGENTA)}

    def load(wants, directory, lookups, *, http, context, cached_only, user_agent):
        calls.append(cached_only)
        if not cached_only:
            raise TaskDeadlineExceeded("the icons' time is up")
        return kept

    monkeypatch.setattr(tech_pulse_module, "get_http_client", lambda: _web())
    monkeypatch.setattr(item_icons, "load", load)

    assert plugin._icons(_payload(plugin), _boards(), cache_only=False) is kept
    assert calls == [False, True]


def test_a_cancelled_page_task_is_not_swallowed_by_the_icons(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    cancelled = threading.Event()
    cancelled.set()
    parent = TaskContext(cancelled, time.monotonic() + 30, time.monotonic)

    def load(*_args, **_kwargs):
        raise TaskCancelled("the page's task was cancelled")

    monkeypatch.setattr(tech_pulse_module, "task_context_or_default", lambda timeout_seconds=None: parent)
    monkeypatch.setattr(tech_pulse_module, "get_http_client", lambda: _web())
    monkeypatch.setattr(item_icons, "load", load)

    with pytest.raises(TaskCancelled):
        plugin._icons(_payload(plugin), _boards(), cache_only=False)


def test_the_icons_get_a_share_of_the_tasks_time_not_more(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    seen = []
    parent = TaskContext(threading.Event(), time.monotonic() + 5, time.monotonic)

    def load(wants, directory, lookups, *, http, context, cached_only, user_agent):
        seen.append(context)
        return {}

    monkeypatch.setattr(tech_pulse_module, "task_context_or_default", lambda timeout_seconds=None: parent)
    monkeypatch.setattr(tech_pulse_module, "get_http_client", lambda: _web())
    monkeypatch.setattr(item_icons, "load", load)

    plugin._icons(_payload(plugin), _boards(), cache_only=False)

    # They end before the task's own deadline, leaving the page its time to draw (here nothing is
    # left for them at all), and never take more than ICON_SECONDS.
    assert seen[0].deadline_monotonic == parent.deadline_monotonic - tech_pulse_module.ICON_RESERVE_SECONDS
    assert seen[0].remaining_seconds() == 0 and seen[0].cancel_event is parent.cancel_event
    roomy = TaskContext(threading.Event(), time.monotonic() + 600, time.monotonic)
    monkeypatch.setattr(tech_pulse_module, "task_context_or_default", lambda timeout_seconds=None: roomy)
    plugin._icons(_payload(plugin), _boards(), cache_only=False)
    assert seen[1].remaining_seconds() <= tech_pulse_module.ICON_SECONDS


class _Device:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"timezone": "America/Los_Angeles", "orientation": "horizontal"}
        return values if key is None else values.get(key, default)


@pytest.mark.parametrize("theme_only", [False, True])
def test_generate_image_hands_the_icons_to_the_page_and_closes_them(tmp_path, monkeypatch, theme_only):
    plugin = _plugin(tmp_path)
    icon = MagicMock()
    asked, drawn = [], []
    payload = _payload(plugin)
    monkeypatch.setattr(plugin, "_now_for_device", lambda device: NOW)
    monkeypatch.setattr(plugin, "_payload", lambda settings, now: payload)
    monkeypatch.setattr(plugin, "_ai_payload", lambda settings, device, now: _boards())
    monkeypatch.setattr(plugin, "_history", lambda *args, **kwargs: {})
    monkeypatch.setattr(plugin, "_write_context", lambda *_args: None)
    monkeypatch.setattr(plugin, "_openrouter", lambda now, cache_only=False: None)
    monkeypatch.setattr(plugin, "_icons", lambda payload, ai, cache_only: (asked.append(cache_only),
                                                                             {"site:anthropic.com": icon})[1])
    monkeypatch.setattr(plugin, "_render_page", lambda *args: (drawn.append(args[-1]),
                                                               Image.new("RGB", (800, 480), "black"))[1])
    settings = {"_inkypi_theme": _theme("night"), **({"_theme_render_only": True} if theme_only else {})}

    plugin.generate_image(settings, _Device())

    assert asked == [theme_only] and drawn == [{"site:anthropic.com": icon}]
    icon.close.assert_called_once_with()
