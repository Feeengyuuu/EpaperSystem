import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.magazine_covers.magazine_covers import (  # noqa: E402
    MAX_PI_SAFE_SOURCE_PIXELS,
    MagazineCovers,
    _ImageCandidateParser,
)


class RecordingLoader:
    def __init__(self):
        self.loaded_paths = []
        self.loaded_sizes = []
        self.resize_flags = []

    def from_file(self, path, dimensions, resize=True, focus_crop=False):
        self.loaded_paths.append(Path(path))
        self.resize_flags.append(resize)
        with Image.open(path) as image:
            self.loaded_sizes.append(image.size)
            if not resize:
                return image.copy().convert("RGB")
        return Image.new("RGB", dimensions, "white")


def test_srcset_candidates_are_ordered_small_to_large():
    parser = _ImageCandidateParser("https://example.com/current")

    assert parser._srcset_urls(
        "large.jpg 2400w, small.jpg 600w, medium.jpg 1200w"
    ) == ["small.jpg", "medium.jpg", "large.jpg"]


def test_oversized_candidate_is_downsampled_before_loader(tmp_path, monkeypatch):
    source_path = tmp_path / "large-cover.jpg"
    Image.new("RGB", (1400, 1400), "black").save(source_path)

    plugin = MagazineCovers({"id": "magazine_covers"})
    loader = RecordingLoader()
    plugin.image_loader = loader
    monkeypatch.setattr(plugin, "_download_candidate_to_temp", lambda _url: source_path)

    image = plugin._download_candidate_image(
        {"url": "https://example.com/large-cover.jpg"},
        (800, 480),
    )

    assert image.size == loader.loaded_sizes[0]
    assert loader.resize_flags == [False]
    assert loader.loaded_sizes
    assert loader.loaded_sizes[0] != (1400, 1400)
    assert loader.loaded_sizes[0][0] * loader.loaded_sizes[0][1] <= MAX_PI_SAFE_SOURCE_PIXELS


def test_oversized_webp_candidate_is_skipped_without_downsample(tmp_path, monkeypatch):
    source_path = tmp_path / "large-cover.webp"
    source_path.write_bytes(b"not really decoded in this test")

    plugin = MagazineCovers({"id": "magazine_covers"})
    loader = RecordingLoader()
    plugin.image_loader = loader
    monkeypatch.setattr(plugin, "_download_candidate_to_temp", lambda _url: source_path)
    monkeypatch.setattr(
        plugin,
        "_source_image_info",
        lambda _path: {
            "width": 2268,
            "height": 2858,
            "pixels": 2268 * 2858,
            "format": "WEBP",
        },
    )
    monkeypatch.setattr(
        plugin,
        "_downsample_to_pi_safe_image",
        lambda _path: (_ for _ in ()).throw(AssertionError("WebP should not be downsampled")),
    )

    try:
        plugin._download_candidate_image(
            {"url": "https://example.com/large-cover.webp"},
            (800, 480),
        )
    except RuntimeError as exc:
        assert "WebP" in str(exc)
    else:
        raise AssertionError("Expected oversized WebP source to be skipped")

    assert loader.loaded_paths == []


def test_pi_safe_candidate_uses_original_download(tmp_path, monkeypatch):
    source_path = tmp_path / "small-cover.jpg"
    Image.new("RGB", (600, 800), "white").save(source_path)

    plugin = MagazineCovers({"id": "magazine_covers"})
    loader = RecordingLoader()
    plugin.image_loader = loader
    monkeypatch.setattr(plugin, "_download_candidate_to_temp", lambda _url: source_path)

    plugin._download_candidate_image(
        {"url": "https://example.com/small-cover.jpg"},
        (800, 480),
    )

    assert loader.loaded_paths == [source_path]
    assert loader.loaded_sizes == [(600, 800)]
    assert loader.resize_flags == [False]


def test_random_order_retries_other_sources_when_queue_has_one_failed_source(monkeypatch):
    sources = [
        {"name": "TIME", "url": "https://example.com/time"},
        {"name": "WIRED Japan", "url": "https://example.com/wired"},
        {"name": "Billboard", "url": "https://example.com/billboard"},
    ]
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setattr(
        plugin,
        "_read_state",
        lambda: {"random_queue": ["WIRED Japan|https://example.com/wired"]},
    )
    monkeypatch.setattr(plugin, "_write_state", lambda _state: None)

    ordered = plugin._random_order(sources)

    assert ordered[0]["name"] == "WIRED Japan"
    assert {source["name"] for source in ordered[1:]} == {"TIME", "Billboard"}


def test_random_failure_removes_source_from_queue(monkeypatch):
    state = {
        "random_queue": [
            "WIRED Japan|https://example.com/wired",
            "TIME|https://example.com/time",
        ]
    }
    writes = []
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setattr(plugin, "_read_state", lambda: dict(state))
    monkeypatch.setattr(plugin, "_write_state", lambda next_state: writes.append(next_state))

    plugin._remember_failure({"name": "WIRED Japan", "url": "https://example.com/wired"})

    assert writes[-1]["random_queue"] == ["TIME|https://example.com/time"]


def test_cover_crop_preserves_top_masthead_area():
    plugin = MagazineCovers({"id": "magazine_covers"})
    source = Image.new("RGB", (800, 1600), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 800, 180), fill="black")
    draw.rectangle((0, 1180, 800, 1600), fill="gray")

    fitted = plugin._fit_cover(
        source,
        (800, 480),
        {"fitMode": "cover", "showSourceLabel": "false"},
        {"name": "Masthead"},
    )

    top_band = fitted.crop((0, 0, 800, 140)).convert("L")
    bottom_band = fitted.crop((0, 340, 800, 480)).convert("L")
    assert sum(top_band.histogram()[:32]) > 80000
    assert sum(bottom_band.histogram()[:32]) == 0


def test_cover_crop_uses_detected_title_band_as_crop_rule():
    plugin = MagazineCovers({"id": "magazine_covers"})
    source = Image.new("RGB", (800, 1600), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 800, 260), fill=(245, 245, 245))
    draw.rectangle((0, 540, 800, 700), fill="black")
    draw.rectangle((120, 575, 680, 665), fill="white")
    draw.rectangle((0, 1180, 800, 1600), fill="gray")

    fitted = plugin._fit_cover(
        source,
        (800, 480),
        {"fitMode": "cover", "showSourceLabel": "false"},
        {"name": "Detected Title"},
    )

    upper_band = fitted.crop((0, 0, 800, 190)).convert("L")
    lower_band = fitted.crop((0, 330, 800, 480)).convert("L")
    assert sum(upper_band.histogram()[:32]) > 70000
    assert sum(lower_band.histogram()[:32]) == 0


def test_source_label_adds_publication_context():
    plugin = MagazineCovers({"id": "magazine_covers"})
    source = Image.new("RGB", (800, 480), "white")

    fitted = plugin._fit_cover(
        source,
        (800, 480),
        {"fitMode": "contain"},
        {"name": "Variety"},
    )

    label_area = fitted.crop((0, 380, 260, 480)).convert("L")
    assert sum(label_area.histogram()[:32]) > 100
