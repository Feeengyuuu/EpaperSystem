import gc
from pathlib import Path
import weakref

from PIL import Image, ImageChops, ImageDraw
import pytest

from utils import local_wordmark


@pytest.fixture(autouse=True)
def empty_wordmark_cache():
    caches = (local_wordmark._load_fitted_wordmark, local_wordmark._load_source_logo, local_wordmark._sized_source_logo)
    for cache in caches:
        cache.cache_clear()
    yield
    for cache in caches:
        cache.cache_clear()


@pytest.mark.parametrize("paper", [(250, 248, 239), (9, 16, 13)])
def test_transparent_padding_is_trimmed_and_original_colors_and_gaps_survive(tmp_path, paper):
    path = tmp_path / "wordmark.png"
    with Image.new("RGBA", (40, 30)) as source:
        draw = ImageDraw.Draw(source)
        draw.rectangle((5, 8, 14, 12), fill=(255, 255, 255, 255))
        draw.rectangle((5, 8, 7, 12), fill=(11, 35, 65, 255))
        draw.point((9, 10), fill=(0, 0, 0, 0))
        draw.point((12, 10), fill=(235, 155, 24, 255))
        source.save(path)
    canvas = Image.new("RGB", (40, 30), paper)
    assert local_wordmark.paste_local_wordmark(canvas, path, (5, 5, 35, 25))
    # Small art remains exactly 10x5; transparent source padding is not scaled.
    assert ImageChops.difference(canvas, Image.new("RGB", canvas.size, paper)).getbbox() == (15, 12, 25, 17)
    assert canvas.getpixel((15, 12)) == (11, 35, 65)
    assert canvas.getpixel((18, 12)) == (255, 255, 255)
    assert canvas.getpixel((19, 14)) == paper
    assert canvas.getpixel((22, 14)) == (235, 155, 24)


def test_shrinking_preserves_the_whole_wide_artwork_and_aspect_ratio(tmp_path):
    path = tmp_path / "wide.png"
    with Image.new("RGBA", (400, 100), (255, 255, 255, 255)) as source:
        draw = ImageDraw.Draw(source)
        draw.rectangle((0, 0, 99, 99), fill=(0, 0, 255, 255))
        draw.rectangle((300, 0, 399, 99), fill=(255, 0, 0, 255))
        source.save(path)
    canvas = Image.new("RGB", (60, 40))
    assert local_wordmark.paste_local_wordmark(canvas, path, (10, 10, 50, 30))
    assert canvas.getbbox() == (10, 15, 50, 25)
    assert canvas.getpixel((10, 20)) == (0, 0, 255)
    assert canvas.getpixel((49, 20)) == (255, 0, 0)


def test_remote_low_alpha_dust_does_not_shrink_letters_but_soft_edges_are_preserved(tmp_path):
    path = tmp_path / "dust.png"
    with Image.new("RGBA", (120, 80)) as source:
        draw = ImageDraw.Draw(source)
        draw.rectangle((40, 30, 79, 49), fill=(11, 35, 65, 255))
        draw.point((1, 1), fill=(255, 255, 255, 1))
        draw.point((118, 78), fill=(255, 255, 255, 2))
        draw.point((39, 29), fill=(255, 255, 255, 2))
        source.save(path)
    canvas = Image.new("RGB", (60, 30))
    assert local_wordmark.paste_local_wordmark(canvas, path, (0, 0, 60, 30))
    visible = canvas.getchannel("B").point(lambda value: 255 if value > 8 else 0)
    assert visible.getbbox() == (10, 5, 50, 25)
    assert canvas.getpixel((10, 5)) == (11, 35, 65)
    # Low alpha inside the safety padding is composited unchanged, not erased.
    assert canvas.getpixel((9, 4)) == (2, 2, 2)


@pytest.mark.parametrize("kind", ["missing", "corrupt", "transparent", "oversized"])
def test_unusable_asset_returns_false_without_changing_the_canvas(tmp_path, kind):
    path = tmp_path / "wordmark.png"
    if kind == "corrupt":
        path.write_bytes(b"not a PNG")
    elif kind in {"transparent", "oversized"}:
        size = (16, 8) if kind == "transparent" else (2049, 1)
        with Image.new("RGBA", size) as source:
            source.save(path)
    canvas = Image.new("RGB", (60, 40), (9, 16, 13))
    before = canvas.tobytes()
    assert local_wordmark.paste_local_wordmark(canvas, path, (5, 5, 55, 35)) is False
    assert canvas.tobytes() == before


def test_bounded_cache_reuses_fitted_art_and_does_not_retain_decoded_sources(tmp_path, monkeypatch):
    slots = local_wordmark._load_fitted_wordmark.cache_info().maxsize
    paths = [tmp_path / f"{index}.png" for index in range(slots + 1)]
    for path in paths:
        with Image.new("RGBA", (1024, 256), (15, 35, 65, 255)) as source:
            source.save(path)
    decoded = []
    original = local_wordmark.safe_open_image

    def track_decode(*args, **kwargs):
        source = original(*args, **kwargs)
        decoded.append(weakref.ref(source))
        return source

    monkeypatch.setattr(local_wordmark, "safe_open_image", track_decode)
    local_wordmark._load_fitted_wordmark.cache_clear()
    canvas = Image.new("RGB", (40, 20))
    order = list(range(slots)) + [0, slots, 1]
    for index in order:
        assert local_wordmark.paste_local_wordmark(canvas, paths[index], (0, 0, 40, 20))
    # The extra asset evicts the least recently used slot (1), so only it is decoded again.
    assert len(decoded) == slots + 2
    assert local_wordmark._load_fitted_wordmark.cache_info().currsize == slots
    gc.collect()
    assert all(reference() is None for reference in decoded)


def test_left_alignment_pins_the_artwork_and_reports_its_box(tmp_path):
    path = tmp_path / "tall.png"
    with Image.new("RGBA", (30, 20)) as source:
        ImageDraw.Draw(source).rectangle((0, 0, 29, 19), fill=(242, 183, 5, 255))
        source.save(path)
    canvas = Image.new("RGB", (100, 40), (255, 255, 255))

    centered = local_wordmark.paste_local_wordmark(canvas.copy(), path, (10, 10, 90, 30))
    pinned = local_wordmark.paste_local_wordmark(canvas, path, (10, 10, 90, 30), align="left")

    assert centered == (35, 10, 65, 30)
    assert pinned == (10, 10, 40, 30)
    assert ImageChops.difference(canvas, Image.new("RGB", canvas.size, "white")).getbbox() == (10, 10, 40, 30)


def test_source_logo_is_cropped_scaled_and_inked_only_where_asked(tmp_path):
    path = tmp_path / "logo.png"
    logo = Image.new("RGBA", (60, 30), (0, 0, 0, 0))
    logo.paste((20, 20, 20, 255), (5, 5, 25, 25))      # black lettering
    logo.paste((80, 20, 160, 255), (25, 5, 45, 25))    # a dark brand purple
    logo.paste((230, 30, 30, 255), (45, 5, 55, 25))    # a bright brand red
    logo.save(path)
    ink = (240, 240, 240)

    plain = local_wordmark.load_source_logo(path, 10)
    inked = local_wordmark.load_source_logo(path, 10, ink)
    dark = local_wordmark.load_source_logo(path, 10, ink, dark_parts=True)

    assert plain.size == (25, 10)
    assert plain.getpixel((4, 5))[:3] == (20, 20, 20)
    assert inked.getpixel((4, 5))[:3] == ink and inked.getpixel((14, 5))[:3] == (80, 20, 160)
    red = dark.getpixel((22, 5))[:3]
    assert dark.getpixel((14, 5))[:3] == ink and red[0] > 220 and max(red[1:]) < 40
    assert local_wordmark.load_source_logo(tmp_path / "missing.png", 10) is None


def _three_part_logo(path):
    logo = Image.new("RGBA", (60, 30), (0, 0, 0, 0))
    logo.paste((20, 20, 20, 255), (5, 5, 25, 25))
    logo.paste((80, 20, 160, 255), (25, 5, 45, 25))
    logo.paste((230, 30, 30, 255), (45, 5, 55, 25))
    logo.save(path)


def test_a_source_logo_is_sized_and_inked_once_and_handed_out_as_the_callers_own(tmp_path, monkeypatch):
    path = tmp_path / "logo.png"
    _three_part_logo(path)
    ink = (240, 240, 240)

    first = local_wordmark.load_source_logo(path, 10, ink, dark_parts=True)
    monkeypatch.setattr(Image.Image, "resize", lambda *args, **kwargs: pytest.fail("the logo was scaled again"))
    again = local_wordmark.load_source_logo(path, 10, list(ink) + [255], dark_parts=True)
    monkeypatch.undo()

    # Scaled and inked once for both draws: the same pixels in two images that do not share them.
    assert local_wordmark._sized_source_logo.cache_info()[:2] == (1, 1)
    assert again is not first and again.size == first.size and again.tobytes() == first.tobytes()
    first.paste((1, 2, 3, 255), (0, 0, 25, 10))
    assert local_wordmark.load_source_logo(path, 10, ink, dark_parts=True).tobytes() == again.tobytes()
    # Another height, ink or treatment is another picture; without ink, dark_parts changes nothing.
    assert local_wordmark.load_source_logo(path, 12, ink, dark_parts=True).size == (30, 12)
    assert local_wordmark.load_source_logo(path, 10, ink).tobytes() != again.tobytes()
    assert local_wordmark.load_source_logo(path, 10, (9, 9, 9), dark_parts=True).tobytes() != again.tobytes()
    plain = local_wordmark.load_source_logo(path, 10)
    assert plain.tobytes() != again.tobytes()
    assert local_wordmark.load_source_logo(path, 10, dark_parts=True).tobytes() == plain.tobytes()
    assert local_wordmark._sized_source_logo.cache_info().currsize == 5
    assert local_wordmark.load_source_logo(path, 0) is None


def test_a_changed_source_logo_file_is_read_again(tmp_path):
    path = tmp_path / "logo.png"
    Image.new("RGBA", (40, 20), (200, 30, 30, 255)).save(path)
    before = local_wordmark.load_source_logo(path, 10)

    Image.new("RGBA", (90, 30), (30, 30, 200, 255)).save(path)
    after = local_wordmark.load_source_logo(path, 10)
    path.unlink()

    assert before.size == (20, 10) and before.getpixel((5, 5))[:3] == (200, 30, 30)
    assert after.size == (30, 10) and after.getpixel((5, 5))[:3] == (30, 30, 200)
    assert local_wordmark.load_source_logo(path, 10) is None


def test_the_source_logo_cache_is_bounded(tmp_path):
    path = tmp_path / "logo.png"
    Image.new("RGBA", (40, 20), (200, 30, 30, 255)).save(path)
    slots = local_wordmark._sized_source_logo.cache_info().maxsize

    for height in range(1, slots + 20):
        assert local_wordmark.load_source_logo(path, height).height == height

    assert 0 < slots <= 256 and local_wordmark._sized_source_logo.cache_info().currsize == slots


def test_a_source_logos_width_is_known_without_scaling_it(tmp_path, monkeypatch):
    path = tmp_path / "logo.png"
    _three_part_logo(path)
    widths = {height: local_wordmark.load_source_logo(path, height).width for height in range(1, 40)}
    monkeypatch.setattr(Image.Image, "resize", lambda *args, **kwargs: pytest.fail("the logo was scaled"))

    assert {height: local_wordmark.source_logo_width(path, height) for height in widths} == widths
    assert local_wordmark.source_logo_width(path, 0) is None
    assert local_wordmark.source_logo_width(tmp_path / "missing.png", 10) is None


def _block(width, height, colour):
    return Image.new("RGBA", (width, height), (*colour, 255))


def _columns(image, colour):
    return [x for x in range(image.width) if any(image.getpixel((x, y)) == colour for y in range(image.height))]


def test_a_header_logo_row_is_centred_and_gives_up_its_spare_logos_then_its_last():
    red, green, blue, gold = (200, 30, 30), (30, 160, 60), (30, 60, 200), (220, 170, 20)
    logos = {"red": _block(30, 20, red), "green": _block(40, 10, green), "missing": None,
             "blue": _block(20, 16, blue), "gold": _block(10, 20, gold)}

    def row(left, right, **options):
        image = Image.new("RGB", (300, 40), (255, 255, 255))
        bounds = local_wordmark.paste_header_logo_row(image, logos, left, right, 20, 10, **options)
        return image, bounds

    # All four (the missing one left out) centred in the span, each centred on the middle line.
    image, bounds = row(50, 250)
    assert bounds == (85, 215)
    assert (_columns(image, red)[0], _columns(image, green)[0], _columns(image, blue)[0], _columns(image, gold)[0]) == (
        85, 125, 175, 205)
    assert image.getpixel((130, 14)) == (255, 255, 255) and image.getpixel((130, 15)) == green
    assert image.getpixel((130, 24)) == green and image.getpixel((130, 25)) == (255, 255, 255)
    # Too narrow by a pixel: the spare logos go first, in their order, then the row's last.
    image, bounds = row(0, 129, spare=("blue", "green"))
    assert bounds[1] - bounds[0] == 100 and not _columns(image, blue) and _columns(image, green)
    image, bounds = row(0, 99, spare=("blue", "green"))
    assert bounds[1] - bounds[0] == 50 and not _columns(image, green) and _columns(image, gold)
    image, bounds = row(0, 49, spare=("blue", "green"))
    assert bounds == (10, 40) and _columns(image, red) and not _columns(image, gold)
    image, bounds = row(0, 29, spare=("blue", "green"))
    assert bounds is None and image.getcolors() == [(300 * 40, (255, 255, 255))]
    # A list of images is a row too.
    image = Image.new("RGB", (300, 40), (255, 255, 255))
    assert local_wordmark.paste_header_logo_row(image, [logos["red"], None, logos["gold"]], 0, 100, 20, 10) == (25, 75)
    assert local_wordmark.paste_header_logo_row(image, [], 0, 100, 20, 10) is None


def test_a_header_logo_row_starts_on_a_whole_pixel_unless_it_keeps_its_exact_centre():
    logos = [_block(31, 10, (200, 30, 30)), _block(20, 10, (30, 60, 200))]
    snapped, exact = (Image.new("RGB", (200, 30), (255, 255, 255)) for _ in range(2))

    # 61 px of logos and gap in a 100 px span: the exact start is 19.5.
    assert local_wordmark.paste_header_logo_row(snapped, logos, 0, 100, 15, 10) == (20, 81)
    assert local_wordmark.paste_header_logo_row(exact, logos, 0, 100, 15, 10, snap=False) == (19.5, 80.5)
    assert _columns(snapped, (30, 60, 200))[0] - _columns(snapped, (200, 30, 30))[-1] == 11
    assert _columns(exact, (200, 30, 30))[0] == round(19.5) and _columns(exact, (30, 60, 200))[0] == round(60.5)


def test_the_pages_name_their_sources_by_logo():
    plugins = Path(local_wordmark.__file__).resolve().parents[1] / "plugins"
    money = plugins / "money_radar" / "assets" / "logos"
    record = (money / "logos.source.json").read_text(encoding="utf-8")
    for name in ("ecb", "fred", "bls", "coingecko", "finnhub"):
        assert (money / f"{name}.png").is_file() and f'"{name}"' in record
    # The logos the money page and the stock page both print are kept once, with the shared ones.
    shared = plugins / "base_plugin" / "assets" / "source_logos"
    record = (shared / "source_logos.source.json").read_text(encoding="utf-8")
    for name in ("sec", "yahoo_finance", "nasdaq"):
        assert (shared / f"{name}.png").is_file() and f'"{name}"' in record
        assert not (money / f"{name}.png").exists()
    assert not list((plugins / "stocktracker" / "assets").glob("sources/*.png"))


@pytest.mark.parametrize("plugin", ["nature_nearby", "tech_play", "planet_now"])
def test_aggregate_pages_do_not_ship_header_art_they_no_longer_draw(plugin):
    assets = Path(local_wordmark.__file__).resolve().parents[1] / "plugins" / plugin / "assets"
    assert not list(assets.glob("header_art*"))
