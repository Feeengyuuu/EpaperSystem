import gc
import weakref

from PIL import Image, ImageChops, ImageDraw
import pytest

from utils import local_wordmark


@pytest.fixture(autouse=True)
def empty_wordmark_cache():
    local_wordmark._load_fitted_wordmark.cache_clear()
    yield
    local_wordmark._load_fitted_wordmark.cache_clear()


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


def test_three_asset_cache_reuses_fitted_art_and_does_not_retain_decoded_sources(tmp_path, monkeypatch):
    paths = [tmp_path / f"{index}.png" for index in range(4)]
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
    canvas = Image.new("RGB", (40, 20))
    for index in (0, 1, 2, 0, 3, 1):
        assert local_wordmark.paste_local_wordmark(canvas, paths[index], (0, 0, 40, 20))
    # Fourth asset evicts the least recently used of the three slots.
    assert len(decoded) == 5
    assert local_wordmark._load_fitted_wordmark.cache_info().currsize == 3
    gc.collect()
    assert all(reference() is None for reference in decoded)
