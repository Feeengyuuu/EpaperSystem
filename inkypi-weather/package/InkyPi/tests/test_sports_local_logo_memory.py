"""Local title decoding releases owned source pixels before returning a cache."""

import hashlib

import pytest
from PIL import Image

from plugins.sports_dashboard import common
from plugins.sports_dashboard.sports_dashboard import SportsDashboard, TEAM_LOGO_CACHE


def test_local_logo_releases_detached_source_and_preserves_alpha(monkeypatch, tmp_path):
    path = tmp_path / 'title.png'
    path.write_bytes(b'fixture')
    source = Image.new('RGBA', (4, 1))
    source.putdata([(10, 20, 30, 0), (40, 50, 60, 7), (70, 80, 90, 8), (110, 120, 130, 255)])
    monkeypatch.setattr(common, 'safe_open_image', lambda *_a, **_k: source)
    TEAM_LOGO_CACHE.clear()
    try:
        logo = SportsDashboard._load_local_logo(str(path), (2, 1), alpha_threshold=8)
        assert logo is not None
        assert [logo.getpixel((x, 0)) for x in range(2)] == [(70, 80, 90, 8), (110, 120, 130, 255)]
        with pytest.raises(ValueError):
            source.getpixel((0, 0))
        assert SportsDashboard._load_local_logo(str(path), (2, 1), alpha_threshold=8) is logo
    finally:
        source.close()
        TEAM_LOGO_CACHE.clear()


@pytest.mark.parametrize(('path', 'digest'), [
    (common.LOCAL_NFL_TITLE_WORDMARK_PATH, '0cbc5041d51d510b383175bb7bd6a9998eb83e5060abe36f2ef3902db0517614'),
])
def test_local_title_pixels_match_preoptimization_reference(path, digest):
    TEAM_LOGO_CACHE.clear()
    try:
        logo = SportsDashboard._load_local_logo(path, (180, 52), alpha_threshold=8)
        assert hashlib.sha256(logo.tobytes()).hexdigest() == digest
    finally:
        TEAM_LOGO_CACHE.clear()


def test_local_logo_releases_owned_images_when_resize_fails(monkeypatch, tmp_path):
    path = tmp_path / 'resize-error.png'
    path.write_bytes(b'fixture')
    source = Image.new('RGBA', (20, 20), (40, 70, 120, 220))
    owned = []
    monkeypatch.setattr(common, 'safe_open_image', lambda *_a, **_k: source)

    def fail_resize(image, *_args, **_kwargs):
        with pytest.raises(ValueError):
            source.getpixel((0, 0))
        owned.append(image)
        raise OSError('resize failed')

    monkeypatch.setattr(common.ImageOps, 'contain', fail_resize)
    TEAM_LOGO_CACHE.clear()
    try:
        assert SportsDashboard._load_local_logo(str(path), (10, 10), alpha_threshold=8) is None
        assert len(owned) == 1
        with pytest.raises(ValueError):
            owned[0].getpixel((0, 0))
    finally:
        source.close()
        TEAM_LOGO_CACHE.clear()
