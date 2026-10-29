"""The official logos of the publishers and magazines the cover pages name.

The logos (assets/publisher_logos, built by tools/build_source_logos.py) are found by the name a
source gives: Comic Vine's or the GCD's publisher, a magazine's title. Names are compared without
case, accents, punctuation or the words every publisher's name carries ("Comics", "Publishing",
"Inc."); a name marked "*" also stands for the titles it begins ("People Special Edition").
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import Image

from utils.safe_image import safe_open_image
from utils.theme_utils import relative_luminance

PUBLISHER_LOGO_DIR = Path(__file__).with_name("assets") / "publisher_logos"
# A logo that fills at least BADGE_COVER of its box with at least BADGE_LIGHT of that near white is a
# badge (light lettering on its own dark or coloured ground): it stays as printed at night, where
# other logos' dark parts (below DARK_LUMINANCE) take the page's ink.
BADGE_LIGHT, BADGE_COVER, DARK_LUMINANCE = 0.02, 0.55, 0.09
NAMES = {
    # Comic publishers (Comic Vine's and the GCD's names for them)
    "marvel": ("Marvel", "Marvel Comics Group", "Marvel Entertainment", "Marvel Worldwide"),
    "dc": ("DC", "DC Comics", "National Periodical Publications", "National Comics Publications",
           "National Allied Publications", "Detective Comics Inc"),
    "image": ("Image", "Image Comics"), "dark_horse": ("Dark Horse", "Dark Horse Comics", "Dark Horse Books"),
    "idw": ("IDW", "IDW Publishing", "Idea and Design Works"), "boom": ("Boom! Studios", "BOOM! Box", "Boom"),
    "dynamite": ("Dynamite", "Dynamite Entertainment"), "oni": ("Oni Press", "Oni-Lion Forge Publishing Group"),
    "archie": ("Archie", "Archie Comics", "Archie Comic Publications", "MLJ"), "valiant": ("Valiant", "Valiant Entertainment"),
    "aftershock": ("AfterShock", "AfterShock Comics"), "awa": ("AWA", "AWA Studios", "Artists Writers & Artisans"),
    "vault": ("Vault", "Vault Comics"), "zenescope": ("Zenescope", "Zenescope Entertainment"),
    "kodansha": ("Kodansha", "Kodansha Comics", "Kodansha USA", "講談社"),
    "shueisha": ("Shueisha", "集英社"), "shogakukan": ("Shogakukan", "小学館"),
    "kadokawa": ("Kadokawa", "Kadokawa Shoten", "Kadokawa Corporation", "KADOKAWA", "角川書店"),
    "square_enix": ("Square Enix", "スクウェア・エニックス"), "viz": ("Viz", "VIZ Media"),
    "yen_press": ("Yen Press", "Yen On"), "seven_seas": ("Seven Seas", "Seven Seas Entertainment"),
    "dc_thomson": ("DC Thomson", "D.C. Thomson & Co.", "D. C. Thomson"),
    "panini": ("Panini", "Panini Comics", "Panini Verlag", "Panini UK", "Panini Group"),
    "egmont": ("Egmont", "Egmont Group", "Egmont Publishing"), "ehapa": ("Egmont Ehapa", "Ehapa", "Egmont Ehapa Media"),
    "glenat": ("Glénat", "Éditions Glénat"), "dargaud": ("Dargaud",), "dupuis": ("Dupuis", "Éditions Dupuis"),
    "casterman": ("Casterman",), "delcourt": ("Delcourt", "Éditions Delcourt"),
    "bonelli": ("Sergio Bonelli Editore", "Bonelli", "Sergio Bonelli"), "dell": ("Dell", "Dell Publishing"),
    "charlton": ("Charlton", "Charlton Comics"), "fawcett": ("Fawcett", "Fawcett Publications", "Fawcett Comics"),
    "ec": ("EC", "EC Comics", "Entertaining Comics", "Educational Comics"), "tokyopop": ("Tokyopop",),
    "humanoids": ("Humanoids", "Les Humanoïdes Associés"), "ahoy": ("Ahoy", "Ahoy Comics"),
    "hakusensha": ("Hakusensha", "白泉社"), "akita_shoten": ("Akita Shoten", "秋田書店"),
    "futabasha": ("Futabasha", "双葉社"), "carlsen": ("Carlsen", "Carlsen Comics", "Carlsen Verlag"),
    "abril": ("Abril", "Editora Abril"), "planeta": ("Planeta DeAgostini", "Planeta Cómic", "Planeta"),
    "lombard": ("Le Lombard", "Lombard"), "fantagraphics": ("Fantagraphics", "Fantagraphics Books"),
    "top_shelf": ("Top Shelf", "Top Shelf Productions"), "scholastic": ("Scholastic", "Scholastic Graphix"),
    "first_second": ("First Second", "First Second Books"), "vertigo": ("Vertigo", "DC Vertigo"),
    "wildstorm": ("WildStorm", "Wildstorm Productions"), "milestone": ("Milestone", "Milestone Media"),
    "atlas": ("Atlas", "Atlas Comics"), "timely": ("Timely", "Timely Comics", "Timely Publications"),
    "quality": ("Quality", "Quality Comics", "Quality Comics Group"), "eclipse": ("Eclipse", "Eclipse Comics"),
    "topps": ("Topps", "Topps Comics"),
    # Magazines (their own titles)
    "time": ("TIME", "Time Magazine"), "rolling_stone": ("Rolling Stone*",), "billboard": ("Billboard*",),
    "vanity_fair": ("Vanity Fair*",), "the_atlantic": ("The Atlantic", "The Atlantic Monthly"),
    "variety": ("Variety", "Daily Variety"), "hollywood_reporter": ("The Hollywood Reporter*",),
    "us_weekly": ("Us Weekly*",),"sports_illustrated": ("Sports Illustrated*",),
    "robb_report": ("Robb Report*",), "readers_digest": ("Reader's Digest*",), "taste_of_home": ("Taste of Home*",),
    "tv_guide": ("TV Guide*",), "people": ("People*", "People Weekly"), "newsweek": ("Newsweek*",),
    "playboy": ("Playboy*",), "penthouse": ("Penthouse*",), "hustler": ("Hustler*",), "maxim": ("Maxim*",),
    "vogue": ("Vogue",), "national_geographic": ("National Geographic*",), "new_yorker": ("The New Yorker*",),
    "life": ("Life", "LIFE Magazine"), "artforum": ("Artforum*",), "art_in_america": ("Art in America*",),
    "womans_world": ("Woman's World*",), "first_for_women": ("First for Women*",), "closer_weekly": ("Closer Weekly*",),
    "entertainment_weekly": ("Entertainment Weekly*",), "esquire": ("Esquire*",), "gq": ("GQ", "Gentlemen's Quarterly"),
    "wired": ("Wired",), "forbes": ("Forbes*",), "economist": ("The Economist*",), "harpers_bazaar": ("Harper's Bazaar*",),
    "elle": ("Elle",), "cosmopolitan": ("Cosmopolitan*",), "popular_science": ("Popular Science*",),
    "popular_mechanics": ("Popular Mechanics*",), "scientific_american": ("Scientific American*",),
    "mad": ("Mad", "MAD Magazine"), "saturday_evening_post": ("The Saturday Evening Post*", "Saturday Evening Post*"),
    "harpers": ("Harper's Magazine", "Harper's Monthly", "Harper's New Monthly Magazine"), "the_nation": ("The Nation",),
    "fortune": ("Fortune",), "businessweek": ("Bloomberg Businessweek*", "Businessweek", "Business Week"),
    "mens_health": ("Men's Health*",), "womens_health": ("Women's Health*",), "allure": ("Allure",),
    "glamour": ("Glamour",), "spin": ("Spin",), "mojo": ("Mojo",), "nme": ("NME", "New Musical Express"),
    "empire": ("Empire",), "i_d": ("i-D",), "interview": ("Interview",), "paris_match": ("Paris Match*",),
    "der_spiegel": ("Der Spiegel*", "Spiegel"), "stern": ("Stern",), "the_spectator": ("The Spectator",),
    "nature": ("Nature",), "science": ("Science",), "weird_tales": ("Weird Tales*",),
    "amazing_stories": ("Amazing Stories*",), "town_country": ("Town & Country*",), "motor_trend": ("Motor Trend*",),
    "road_track": ("Road & Track*",), "outside": ("Outside",), "sporting_news": ("The Sporting News*", "Sporting News*"),
    "jet": ("Jet",), "teen_vogue": ("Teen Vogue*",), "marie_claire": ("Marie Claire*",), "instyle": ("InStyle*",),
    "w": ("W Magazine",), "aperture": ("Aperture",), "architectural_digest": ("Architectural Digest*", "AD"),
    "car_and_driver": ("Car and Driver*",), "golf_digest": ("Golf Digest*",), "new_republic": ("The New Republic*",),
    "the_face": ("The Face",), "boys_life": ("Boys' Life*", "Scout Life"), "smithsonian": ("Smithsonian*",),
    "essence": ("Essence",), "ebony": ("Ebony",), "field_stream": ("Field & Stream*", "Field and Stream*"),
    "better_homes": ("Better Homes & Gardens*", "Better Homes and Gardens*"),
}
_GENERIC = frozenset({"the", "inc", "incorporated", "ltd", "limited", "llc", "co", "corp", "corporation", "company",
                      "group", "publishing", "publisher", "publishers", "publication", "publications", "comic",
                      "comics", "magazine", "magazines", "press", "books", "entertainment", "studio", "studios",
                      "media", "usa", "us", "editions", "edition", "verlag", "editora", "editore", "editorial",
                      "and"})


def _words(name):
    """``name`` as plain words: accents dropped (from a Latin name; a Japanese one stays), "&" as
    "and", punctuation gone, lower case."""
    text = str(name or "")
    if re.search(r"[A-Za-z]", text):
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"['’.]", "", text.replace("&", " and ").casefold())
    return [word for word in re.split(r"[^\w]+", text) if word]


def _key(words):
    kept = [word for word in words if word not in _GENERIC]
    return " ".join(kept or words)


def _tables():
    exact, prefix = {}, {}
    for logo, names in NAMES.items():
        for name in names:
            words = _words(name.rstrip("*"))
            exact.setdefault(_key(words), logo)
            if name.endswith("*"):
                prefix.setdefault(" ".join(words), logo)
    return exact, prefix


_EXACT, _PREFIX = _tables()


def logo_key(name):
    """The logo for a publisher or magazine called ``name``, or None."""
    words = _words(name)
    if not words:
        return None
    found = _EXACT.get(_key(words))
    if found:
        return found
    for size in range(len(words) - 1, 0, -1):
        found = _PREFIX.get(" ".join(words[:size]))
        if found:
            return found
    return None


@lru_cache(maxsize=64)
def _art(key):
    try:
        with safe_open_image(PUBLISHER_LOGO_DIR / f"{key}.png") as source:
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


@lru_cache(maxsize=64)
def _is_badge(key):
    art = _art(key)
    alpha = art.getchannel("A").point(lambda value: 255 if value > 128 else 0)
    drawn = alpha.histogram()[255]
    light = Image.new("L", art.size, 0)
    light.paste(art.convert("L").point(lambda value: 255 if value > 225 else 0), (0, 0), alpha)
    return drawn >= BADGE_COVER * art.width * art.height and light.histogram()[255] >= BADGE_LIGHT * drawn


def publisher_logo(name, box, ink=None):
    """The logo of the publisher or magazine called ``name`` contained in ``box`` (width, height),
    or None without one. With ``ink`` (a dark page) its dark parts take that colour, unless it is
    a badge; its coloured parts stay as printed."""
    key = logo_key(name)
    art = _art(key) if key else None
    if art is None:
        return None
    scale = min(box[0] / art.width, box[1] / art.height)
    logo = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))), Image.Resampling.LANCZOS)
    if ink is not None and not _is_badge(key):
        pixels = logo.load()
        for y in range(logo.height):
            for x in range(logo.width):
                red, green, blue, alpha = pixels[x, y]
                if alpha and relative_luminance((red, green, blue)) < DARK_LUMINANCE:
                    pixels[x, y] = (*ink[:3], alpha)
    return logo
